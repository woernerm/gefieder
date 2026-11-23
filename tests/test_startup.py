"""The stack starts cleanly: every container runs, healthchecks pass, nothing loops.

Asserted positively -- containers reach running and healthy, none restarts -- rather than
by grepping logs for "error", which the database, Grafana and Django all emit harmlessly.
"""
import pytest

from conftest import CONTAINERS, RESTART_TIMEOUT, inspect_container, poll


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
        # A crash-looping container shows a rising restart count.
        assert _inspect(container)["RestartCount"] == 0, f"{container} has restarted"

    @pytest.mark.parametrize("container", ["postgresql", "crudman"])
    def test_all_containers_shall_pass_their_healthchecks(self, container):
        # These two gate the rest of the pod. A container may still be within its
        # start_period when the apps already answer, so poll until it settles.
        def health():
            return _inspect(container)["State"].get("Health", {}).get("Status")
        assert poll(lambda: health() == "healthy", RESTART_TIMEOUT), (
            f"{container} health is {health()!r}"
        )
