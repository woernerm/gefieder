"""Grafana provisioning: the dashboards shipped in the image are loaded at startup.

The build bakes grafana/provisioning/ into the image (rendered by grafana/render.sh), so a
freshly started Grafana must already expose the dashboards without anyone importing them.
This asserts the provisioning actually took effect on the running instance, rather than just
that the files exist in the repository.
"""
import httpx
import pytest

from conftest import (
    APP_NAME, BASE_URL, GRAFANA_PATH, SUPERUSER_NAME, SUPERUSER_PASSWORD, VERIFY_TLS,
)


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
