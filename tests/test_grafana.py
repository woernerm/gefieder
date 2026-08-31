"""What the Grafana image brings with it: the shipped dashboards and the panel plugins.

The build bakes grafana/provisioning/ into the image, so a freshly started Grafana must
expose the dashboards without anyone importing them. Asserted on the running instance
rather than from the repository files. The plugins likewise: they are installed outside
/var/lib/grafana precisely so the data volume cannot hide them.
"""
import os

import httpx
import pytest

from conftest import (
    APP_NAME, BASE_URL, GRAFANA_PATH, SUPERUSER_NAME, SUPERUSER_PASSWORD, VERIFY_TLS,
)

# Straight from GRAFANA_PLUGINS in buildtime.env, so trimming that list does not leave
# these tests asserting on plugins the image no longer contains.
PLUGINS = [
    plugin.strip()
    for plugin in os.environ.get("GRAFANA_PLUGINS", "").split(",")
    if plugin.strip()
]


@pytest.fixture(scope="module")
def grafana_api():
    """An HTTP client for Grafana's API, authenticated as the admin (the superuser).

    The quadlet sets GF_SECURITY_ADMIN_USER and its password from the superuser secret,
    so the database superuser's credentials log in here too.
    """
    with httpx.Client(base_url=f"{BASE_URL}/{GRAFANA_PATH}", verify=VERIFY_TLS,
                      auth=(SUPERUSER_NAME, SUPERUSER_PASSWORD),
                      follow_redirects=True, timeout=10) as client:
        yield client


class TestDashboardProvisioning:
    """The dashboards baked into the image are present on the running Grafana."""

    def test_at_least_one_dashboard_shall_be_provisioned(self, grafana_api):
        # type=dash-db filters folders out of the search API. A provisioned instance
        # returns at least the shipped server-monitoring dashboard.
        resp = grafana_api.get("/api/search", params={"type": "dash-db"})
        assert resp.status_code == 200, f"grafana search failed: {resp.status_code}"
        dashboards = resp.json()
        assert len(dashboards) >= 1, "no dashboard was provisioned on the running grafana"

    def test_the_server_monitoring_dashboard_shall_be_in_the_default_folder(self, grafana_api):
        # foldersFromFilesStructure derives the folder from the on-disk directory, and
        # the server-monitoring JSON lives under dashboards/Default/. Grafana reports it
        # as folderTitle, empty for a dashboard at the root.
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
        """An empty list would drop the test below and leave the suite green."""
        assert PLUGINS, (
            "GRAFANA_PLUGINS is empty; run-tests.sh exports it from buildtime.env"
        )

    @pytest.mark.parametrize("plugin", PLUGINS)
    def test_the_configured_plugin_shall_be_loaded(self, plugin, loaded_plugins):
        # Grafana lists a plugin only if it found it under paths.plugins and accepted it,
        # so this covers both the location and the signature.
        assert plugin in loaded_plugins, (
            f"{plugin} is in GRAFANA_PLUGINS but grafana did not load it"
        )
        assert loaded_plugins[plugin] == "valid", (
            f"{plugin} loaded with signature {loaded_plugins[plugin]!r}, expected 'valid'"
        )
