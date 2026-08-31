"""The system restarts itself after a failure, and volume data survives a restart.

These kill containers, so they run after the read-only startup, http and database tests
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

    Anchored on the id rather than on State.Running: systemd recreates the container
    instead of restarting it in place, and `podman kill` returns once the signal is sent,
    so a liveness check can pass on the *old* container. Requiring a new id makes the
    check independent of how long the recreate takes.
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
        # sqlmesh is the safest to kill: no inbound traffic, no other test's connection.
        old_id = _container_id("sqlmesh")
        assert old_id, "sqlmesh was not running before the kill"

        podman("kill", "sqlmesh")
        assert _wait_replaced("sqlmesh", old_id), (
            "sqlmesh was not restarted after being killed"
        )


class TestVolumePersistence:
    """Data written to a named volume outlives a container restart."""

    def test_database_data_shall_survive_a_restart(self, admin_db):
        # A marker row has to survive the restart.
        with admin_db.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS public.persistence_probe (id int)")
            cur.execute("INSERT INTO public.persistence_probe VALUES (42)")

        podman("restart", "postgresql")

        # admin_db is self-healing (see conftest): the next cursor() reconnects, retrying
        # until the server is back. The same healing is what lets later tests work.
        with admin_db.cursor() as cur:
            cur.execute("SELECT id FROM public.persistence_probe")
            row = cur.fetchone()
            cur.execute("DROP TABLE public.persistence_probe")
        assert row is not None and row[0] == 42
