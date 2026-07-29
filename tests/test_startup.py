"""The stack starts cleanly: every container runs, healthchecks pass, nothing loops.

Startup health is asserted positively (containers reach running/healthy, no restarts)
rather than by grepping logs for the word "error", which the database, Grafana and
Django all emit harmlessly at startup.
"""
import time

import pytest

from conftest import CONTAINERS, RESTART_TIMEOUT, inspect_container


def _inspect(container):
    """Inspect a container, failing with a clear message when it does not exist."""
    info = inspect_container(container)
    assert info is not None, f"container {container} does not exist"
    return info


class TestStartup:
    """The container system comes up cleanly."""

    @pytest.mark.parametrize("container", CONTAINERS)
    def test_all_containers_shall_be_running(self, container):
        state = _inspect(container)["State"]
        assert state["Running"] is True, f"{container} is not running: {state.get('Status')}"

    @pytest.mark.parametrize("container", CONTAINERS)
    def test_no_container_shall_have_restarted_during_startup(self, container):
        # A crash-looping container (e.g. failed provisioning) shows a rising restart count.
        assert _inspect(container)["RestartCount"] == 0, f"{container} has restarted"

    @pytest.mark.parametrize("container", ["postgresql", "crudman"])
    def test_all_containers_shall_pass_their_healthchecks(self, container):
        # These two gate the rest of the pod (the others depend on the database and the
        # app being healthy), so the suite checks their healthchecks. A container may
        # still be within its start_period when the apps already answer, so poll until
        # it settles.
        deadline = time.time() + RESTART_TIMEOUT
        while True:
            health = _inspect(container)["State"].get("Health", {}).get("Status")
            if health == "healthy" or time.time() > deadline:
                break
            time.sleep(2)
        assert health == "healthy", f"{container} health is {health!r}"
