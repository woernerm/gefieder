"""Resilience behaviours docs/decisions.md describes: the system restarts itself after a failure,
and data in the named volumes survives a container restart.

These tests stop/kill containers, so they run after the read-only startup/http/db tests
(pytest collects files alphabetically) and restore the stack as they go.
"""
import time

from conftest import inspect_container, podman


def _container_id(container):
    """Return the container's id, or None while it does not exist."""
    info = inspect_container(container)
    return info["Id"] if info else None


def _wait_replaced(container, old_id, timeout=180):
    """Wait until `container` is running under a different id than `old_id`.

    Anchored on the container id rather than on State.Running, because systemd recreates
    the container instead of restarting it in place: a plain liveness check can pass on
    the *old* container, since `podman kill` returns once the signal is sent rather than
    once the process is gone. Requiring a new id makes the check independent of how long
    the recreate takes, so a slow machine only waits longer instead of reporting a
    spurious pass or failure.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = inspect_container(container)
        if info and info["Id"] != old_id and info["State"]["Running"] is True:
            return True
        time.sleep(2)
    return False


class TestAutoRestart:
    """Restart=always brings a failed container back up (the system self-heals)."""

    def test_a_killed_container_shall_be_restarted(self):
        # sqlmesh is the safest to kill: it owns no inbound traffic and no other test's
        # connection. Killing it makes systemd (Restart=always) bring the service back.
        old_id = _container_id("sqlmesh")
        assert old_id, "sqlmesh was not running before the kill"

        podman("kill", "sqlmesh")
        assert _wait_replaced("sqlmesh", old_id), (
            "sqlmesh was not restarted after being killed"
        )


class TestVolumePersistence:
    """Data written to a named volume outlives a container restart."""

    def test_database_data_shall_survive_a_restart(self, admin_db):
        # Write a marker row, restart postgresql, and confirm the row is still there.
        with admin_db.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS public.persistence_probe (id int)")
            cur.execute("INSERT INTO public.persistence_probe VALUES (42)")

        podman("restart", "postgresql")

        # admin_db is a self-healing connection (see conftest): the restart closed the
        # backend, and the next cursor() reconnects, retrying until the server is back. The
        # same healing is what lets every later test (e.g. test_tenants) keep working.
        with admin_db.cursor() as cur:
            cur.execute("SELECT id FROM public.persistence_probe")
            row = cur.fetchone()
            cur.execute("DROP TABLE public.persistence_probe")
        assert row is not None and row[0] == 42
