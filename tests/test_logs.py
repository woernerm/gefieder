"""Persistent logging: every service logs to stdout/stderr and podman forwards the stream
to journald, so the log survives a crash, a restart and the container being replaced.

This is the behaviour CLAUDE.md requires: the services log to the journal, which persists
and rotates them, rather than to a file on a data volume. Reading a service's log needs no
`podman unshare`, because journalctl reads the journal rather than a file owned by a user
inside the container's namespace.
"""
import re
import subprocess
import time

import pytest

from conftest import LOGGING_UNITS

# A line is considered timestamped if it carries a calendar date and clock time somewhere
# in it. journald stamps every entry it records with its own reception time, and this
# additionally accepts the formats the services emit themselves: ISO-8601
# "2026-06-25T22:31:05" (postgresql's log_line_prefix, grafana's mid-line t=...),
# gunicorn's bracketed "[2026-06-25 22:31:05 +0000]" and nginx's "2026/06/25 22:31:05".
TIMESTAMP = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}")


def journal(unit, *args):
    """Return the journal of one service's systemd unit.

    short-iso rather than the default format, whose syslog-style "Jul 31 14:37:43" stamp
    carries no year and so cannot place a line in time on its own.
    """
    out = subprocess.run(
        ["journalctl", "--user", "-u", f"{unit}.service", "--no-pager", "-o", "short-iso",
         *args],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _journal_lines(unit, timeout=60):
    """The unit's journal lines, waiting for the first one to appear.

    Polled, because some services (grafana, postgresql) emit their first line only after
    the HTTP/DB readiness the suite already waits for.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        lines = [ln for ln in journal(unit).splitlines() if ln.strip()]
        # journalctl prints a "-- No entries --" placeholder when the unit logged nothing.
        lines = [ln for ln in lines if not ln.startswith("-- ")]
        if lines:
            return lines
        time.sleep(2)
    return []


class TestPersistentLogs:
    """Each service's log is captured by journald."""

    @pytest.mark.parametrize("unit", LOGGING_UNITS)
    def test_each_service_shall_write_a_persistent_log(self, unit):
        assert _journal_lines(unit), f"{unit} wrote nothing to its journal"

    def test_a_restart_shall_not_lose_the_persistent_log(self):
        # The journal belongs to the unit, not the container, so a restart appends to it
        # rather than starting over -- the point of the whole arrangement: a crashed
        # container's output is still there afterwards to diagnose it from.
        before = len(_journal_lines("crudman"))
        subprocess.run(
            ["systemctl", "--user", "restart", "crudman.service"], check=True,
        )
        deadline = time.time() + 60
        while time.time() < deadline and len(_journal_lines("crudman")) <= before:
            time.sleep(2)
        after = len(_journal_lines("crudman"))
        assert after >= before, "the journal lost lines across the restart"
        assert after > before, "the restart appended nothing to the journal"

    def test_the_log_shall_be_readable_without_unshare(self):
        # journalctl --user reads the journal directly, so no `podman unshare` is needed
        # to get at a log written by a non-root user inside a container.
        assert _journal_lines("postgresql"), "postgresql's journal is not readable"


class TestLogTimestamps:
    """Every line of every log carries a timestamp, so a line can be placed in time when
    diagnosing a crash. journald records one for each entry it captures."""

    @pytest.mark.parametrize("unit", LOGGING_UNITS)
    def test_every_log_line_shall_carry_a_timestamp(self, unit):
        lines = _journal_lines(unit)
        assert lines, f"{unit} wrote nothing to its journal"
        missing = [ln for ln in lines if not TIMESTAMP.search(ln)]
        assert not missing, (
            f"{unit}'s journal has {len(missing)} line(s) without a timestamp, "
            f"e.g.: {missing[0]!r}"
        )
