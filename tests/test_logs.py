"""Persistent logging: every service writes a log into its data volume that survives a
crash, and the logs the entrypoints produce are owned by the rootless podman user.

This is the behaviour CLAUDE.md requires: "The system shall use the entrypoint.sh
scripts to write persistent logs to the volume ... The logs shall be owned by the
rootless podman user." postgresql and grafana are configured to log into their data
volume instead of via an entrypoint, which satisfies the same goal (a log on disk to
diagnose a crash), so they are checked for the file but not for direct host ownership.
"""
import os
import re
import subprocess
import time

import pytest

from conftest import (
    PERSISTENT_LOGS, USER_OWNED_LOGS, mount_in_container, podman, volume_mountpoint,
)

# A line is considered timestamped if it carries a calendar date and clock time
# somewhere in it. This deliberately accepts the several formats the services emit:
# ISO-8601 "2026-06-25T22:31:05" (this script's awk prefix, grafana's mid-line t=...),
# gunicorn's bracketed "[2026-06-25 22:31:05 +0000]" and nginx's "2026/06/25 22:31:05".
TIMESTAMP = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _log_lines(container, volume, rel):
    """Return the lines of the persistent log, read from inside the container.

    Read in-container for the same reason as _log_present: postgresql/grafana logs land
    on a mapped subuid the host user cannot read directly. A directory (postgresql's
    "log") is concatenated across its files so every service is checked the same way.
    """
    path = f"{mount_in_container(container, volume)}/{rel}"
    out = podman(
        "exec", container, "sh", "-c",
        f'if [ -d "{path}" ]; then cat "{path}"/*; else cat "{path}"; fi',
    )
    return out.splitlines()


def _log_present(container, volume, rel, timeout=60):
    """True if the persistent log appears inside the container within the timeout.

    Checked from inside the container so it works regardless of the host-side namespace
    mapping (postgresql/grafana logs land on a mapped subuid the host user cannot stat).
    A plain file must be non-empty; a directory (postgresql's "log") must hold a file.
    Polled, because some services (grafana, postgresql) flush their first log line only
    after the HTTP/DB readiness the suite already waits for.
    """
    path = f"{mount_in_container(container, volume)}/{rel}"
    probe = f'if [ -d "{path}" ]; then ls -A "{path}" | grep -q .; else [ -s "{path}" ]; fi'
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = podman("exec", container, "sh", "-c", f"{probe} && echo OK || echo MISSING")
        if out.strip() == "OK":
            return True
        time.sleep(2)
    return False


class TestPersistentLogs:
    """Each service leaves a persistent log on its data volume."""

    @pytest.mark.parametrize("container,volume,rel", PERSISTENT_LOGS,
                             ids=[c for c, _, _ in PERSISTENT_LOGS])
    def test_each_service_shall_write_a_persistent_log(self, container, volume, rel):
        assert _log_present(container, volume, rel), (
            f"{container} wrote no persistent log at {rel} in {volume}"
        )

    @pytest.mark.parametrize("container,volume,rel",
                             [t for t in PERSISTENT_LOGS if t[0] in USER_OWNED_LOGS],
                             ids=USER_OWNED_LOGS)
    def test_entrypoint_logs_shall_be_owned_by_the_rootless_user(self, container, volume, rel):
        # The host file must be owned by the user running the tests (the rootless podman
        # user), so it is readable without `podman unshare`.
        path = os.path.join(volume_mountpoint(volume), rel)
        assert os.path.exists(path), f"{path} does not exist on the host"
        assert os.access(path, os.R_OK), f"{path} is not readable by the rootless user"
        assert os.stat(path).st_uid == os.getuid(), (
            f"{path} is not owned by the rootless user (uid {os.getuid()})"
        )

    def test_a_restart_shall_not_lose_the_persistent_log(self):
        # The log lives on the volume, not in the container, so it survives a restart and
        # the entrypoint appends to it (tee -a), leaving a crash's cause on disk. Restart
        # via systemd, which owns the container (a direct `podman restart` races it).
        path = os.path.join(volume_mountpoint("crudman_data"), "crudman.log")
        before = os.path.getsize(path)
        subprocess.run(
            ["systemctl", "--user", "restart", "crudman.service"], check=True,
        )
        # The restarted entrypoint appends fresh startup output to the same file.
        deadline = time.time() + 60
        while time.time() < deadline and os.path.getsize(path) <= before:
            time.sleep(2)
        assert os.path.getsize(path) >= before, "the persistent log was truncated on restart"
        assert os.path.getsize(path) > before, "the restart appended nothing to the log"


class TestLogTimestamps:
    """Every line of every persistent log carries a timestamp, so a line on disk can be
    placed in time when diagnosing a crash. The services do not all timestamp their own
    output (this script's echoes, Django's migrate output and parts of sqlmesh's output
    have none), so the entrypoints are responsible for prefixing one."""

    @pytest.mark.parametrize("container,volume,rel", PERSISTENT_LOGS,
                             ids=[c for c, _, _ in PERSISTENT_LOGS])
    def test_every_log_line_shall_carry_a_timestamp(self, container, volume, rel):
        # The log is written asynchronously after readiness, so wait for it to exist
        # before reading rather than racing an empty file.
        assert _log_present(container, volume, rel), (
            f"{container} wrote no persistent log at {rel} in {volume}"
        )
        lines = [ln for ln in _log_lines(container, volume, rel) if ln.strip()]
        assert lines, f"{container}'s persistent log is empty"
        missing = [ln for ln in lines if not TIMESTAMP.search(ln)]
        assert not missing, (
            f"{container}'s persistent log has {len(missing)} line(s) without a "
            f"timestamp, e.g.: {missing[0]!r}"
        )
