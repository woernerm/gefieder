"""Resilience behaviours CLAUDE.md describes: the system restarts itself after a failure,
and data in the named volumes survives a container restart.

These tests stop/kill containers, so they run after the read-only startup/http/db tests
(pytest collects files alphabetically) and restore the stack as they go.
"""
import time

import pytest

from conftest import SUPERUSER_NAME, _connect, inspect, podman


def _wait_running(container, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if inspect(container)["State"]["Running"] is True:
                return True
        except Exception:  # noqa: BLE001 - container may be mid-recreate
            pass
        time.sleep(2)
    return False


class TestAutoRestart:
    """Restart=always brings a failed container back up (the system self-heals)."""

    def test_a_killed_container_shall_be_restarted(self):
        # sqlmesh is the safest to kill: it owns no inbound traffic and no other test's
        # connection. Killing it makes systemd (Restart=always) bring the service back.
        # The container may be recreated (resetting RestartCount), so assert on liveness.
        podman("kill", "sqlmesh")
        assert _wait_running("sqlmesh"), "sqlmesh was not restarted after being killed"


class TestVolumePersistence:
    """Data written to a named volume outlives a container restart."""

    def test_database_data_shall_survive_a_restart(self, admin_db):
        # Write a marker row, restart postgresql, and confirm the row is still there.
        with admin_db.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS public.persistence_probe (id int)")
            cur.execute("INSERT INTO public.persistence_probe VALUES (42)")

        podman("restart", "postgresql")

        # Reconnect after the restart (the old connection is now dead) and read it back.
        deadline = time.time() + 60
        last_err = None
        while time.time() < deadline:
            try:
                conn = _connect(SUPERUSER_NAME)
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM public.persistence_probe")
                    row = cur.fetchone()
                    cur.execute("DROP TABLE public.persistence_probe")
                conn.close()
                assert row is not None and row[0] == 42
                return
            except Exception as exc:  # noqa: BLE001 - retry until the DB is back
                last_err = exc
                time.sleep(2)
        pytest.fail(f"database did not return after restart: {last_err}")
