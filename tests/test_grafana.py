"""What the Grafana image brings with it: the shipped dashboards and the panel plugins.

The build bakes grafana/provisioning/ into the image (rendered by grafana/render.sh), so a
freshly started Grafana must already expose the dashboards without anyone importing them.
This asserts the provisioning actually took effect on the running instance, rather than just
that the files exist in the repository. The plugins are checked the same way, on the running
instance, because they are the case most likely to break in silence: they are installed
outside /var/lib/grafana precisely so the data volume cannot hide them.
"""
import os

import httpx
import pytest

from conftest import (
    APP_NAME, BASE_URL, GRAFANA_PATH, SUPERUSER_NAME, SUPERUSER_PASSWORD, VERIFY_TLS,
)

# The plugins the build was told to ship, straight from GRAFANA_PLUGINS in buildtime.env
# (run-tests.sh exports it), so trimming that list does not leave these tests asserting on
# plugins the image no longer contains.
PLUGINS = [
    plugin.strip()
    for plugin in os.environ.get("GRAFANA_PLUGINS", "").split(",")
    if plugin.strip()
]


@pytest.fixture(scope="module")
def grafana_api():
    """An HTTP client for Grafana's API, authenticated as the admin (the superuser).

    The grafana quadlet sets GF_SECURITY_ADMIN_USER to the superuser name and its password
    to the superuser secret, so the same credentials the database superuser uses log in here.
    """
    with httpx.Client(base_url=f"{BASE_URL}/{GRAFANA_PATH}", verify=VERIFY_TLS,
                      auth=(SUPERUSER_NAME, SUPERUSER_PASSWORD),
                      follow_redirects=True, timeout=10) as client:
        yield client


class TestDashboardProvisioning:
    """The dashboards baked into the image are present on the running Grafana."""

    def test_at_least_one_dashboard_shall_be_provisioned(self, grafana_api):
        # The search API lists every dashboard Grafana knows about; type=dash-db filters out
        # folders. A provisioned instance returns at least the shipped server-monitoring one.
        resp = grafana_api.get("/api/search", params={"type": "dash-db"})
        assert resp.status_code == 200, f"grafana search failed: {resp.status_code}"
        dashboards = resp.json()
        assert len(dashboards) >= 1, "no dashboard was provisioned on the running grafana"

    def test_the_server_monitoring_dashboard_shall_be_in_the_default_folder(self, grafana_api):
        # The provider derives each dashboard's folder from its on-disk directory
        # (foldersFromFilesStructure), and the server-monitoring JSON lives under
        # dashboards/Default/, so it must land in a "Default" folder rather than the root
        # "General" one. Grafana reports a dashboard's folder in the search result's
        # folderTitle (absent/empty when the dashboard sits at the root).
        resp = grafana_api.get("/api/search", params={"type": "dash-db"})
        assert resp.status_code == 200, f"grafana search failed: {resp.status_code}"
        dashboards = resp.json()
        monitoring = [d for d in dashboards if d.get("uid") == f"{APP_NAME}-server-monitoring"]
        assert monitoring, "the server-monitoring dashboard is not provisioned"
        folder = monitoring[0].get("folderTitle")
        assert folder == "Default", (
            f"server-monitoring dashboard is in folder {folder!r}, expected 'Default' "
            "(it must be provisioned into the Default folder, not the root)"
        )


@pytest.fixture(scope="module")
def loaded_plugins(grafana_api):
    """Map of plugin id -> signature state, as the running Grafana reports it."""
    resp = grafana_api.get("/api/plugins")
    assert resp.status_code == 200, f"grafana plugin list failed: {resp.status_code}"
    return {plugin["id"]: plugin.get("signature") for plugin in resp.json()}


class TestPanelPlugins:
    """The panel plugins baked into the image are loaded by the running Grafana."""

    def test_the_suite_shall_know_which_plugins_to_expect(self):
        """The setting reached pytest at all.

        An empty list would drop the parametrized test below and leave the suite
        green without having checked anything."""
        assert PLUGINS, (
            "GRAFANA_PLUGINS is empty; run-tests.sh exports it from buildtime.env"
        )

    @pytest.mark.parametrize("plugin", PLUGINS)
    def test_the_configured_plugin_shall_be_loaded(self, plugin, loaded_plugins):
        # Grafana lists a plugin here only if it found it under paths.plugins and accepted
        # it, so this catches both halves at once: the image put the files somewhere the
        # data volume does not cover, and the signature still verifies on this Grafana.
        assert plugin in loaded_plugins, (
            f"{plugin} is in GRAFANA_PLUGINS but grafana did not load it"
        )
        assert loaded_plugins[plugin] == "valid", (
            f"{plugin} loaded with signature {loaded_plugins[plugin]!r}, expected 'valid'"
        )
