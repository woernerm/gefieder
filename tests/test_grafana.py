"""What the Grafana image brings with it: the shipped dashboards and the panel plugins.

The build bakes grafana/provisioning/ into the image, so a freshly started Grafana must
expose the dashboards without anyone importing them. Asserted on the running instance
rather than from the repository files. The plugins likewise: they are installed outside
/var/lib/grafana precisely so the data volume cannot hide them.
"""
import os
import re

import httpx
import pytest

from conftest import (
    APP_NAME, BASE_URL, GRAFANA_PATH, MCP_PATH, SUPERUSER_NAME, SUPERUSER_PASSWORD,
    VERIFY_TLS,
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


class TestHomeDashboard:
    """The home page is ours, not Grafana's built-in one.

    Grafana's own home page carries a "Basics" panel whose cards are compiled into the
    frontend bundle, so a card cannot be added to it; default_home_dashboard_path replaces
    the whole page instead. These assert the replacement actually took, because a wrong
    path is not an error: Grafana quietly falls back to the built-in page.
    """

    def test_the_home_dashboard_shall_be_the_provisioned_one(self, grafana_api):
        resp = grafana_api.get("/api/dashboards/home")
        assert resp.status_code == 200, f"home dashboard failed: {resp.status_code}"
        dashboard = resp.json().get("dashboard", {})
        assert dashboard.get("uid") == f"{APP_NAME}-home", (
            f"the home page is {dashboard.get('uid')!r}, not the provisioned one; "
            "default_home_dashboard_path in custom.ini did not take"
        )

    def test_the_home_page_shall_link_to_the_assistant_instructions(self, grafana_api):
        """The card this exists for: a link that 404s is worse than no card."""
        home = grafana_api.get("/api/dashboards/home").json()["dashboard"]
        content = "".join(panel.get("options", {}).get("content", "")
                          for panel in home.get("panels", []))
        assert f"{APP_NAME}-ai-assistant" in content, (
            "the home page has no card linking to the assistant instructions"
        )
        target = grafana_api.get(f"/api/dashboards/uid/{APP_NAME}-ai-assistant")
        assert target.status_code == 200, (
            f"the card links to {APP_NAME}-ai-assistant, which is not provisioned "
            f"({target.status_code})"
        )

    def test_every_card_link_shall_resolve(self, grafana_api):
        """A relative href resolves against the dashboard's own URL, not the Grafana root,
        so it 404s. Grafana's own dashboard URLs are absolute below GRAFANA_PATH, and the
        cards have to be written the same way."""
        home = grafana_api.get("/api/dashboards/home").json()["dashboard"]
        content = "".join(panel.get("options", {}).get("content", "")
                          for panel in home.get("panels", []))
        links = re.findall(r'href="([^"]+)"', content)
        assert links, "the home page has no links at all"
        for link in links:
            assert link.startswith(f"/{GRAFANA_PATH}/"), (
                f"{link!r} does not start with /{GRAFANA_PATH}/; a relative or root-level "
                "link does not resolve when Grafana is served from a subpath"
            )
            # Followed against the running stack, so a typo in a uid fails here.
            resp = grafana_api.get(link.removeprefix(f"/{GRAFANA_PATH}"))
            assert resp.status_code == 200, (
                f"the card link {link} answers {resp.status_code}"
            )

    def test_the_cards_shall_not_rely_on_a_style_block(self, grafana_api):
        """The text panel's sanitizer drops <style>, which would leave the cards unstyled:
        the markup renders, but as a bare list of text. Inline style attributes survive."""
        home = grafana_api.get("/api/dashboards/home").json()["dashboard"]
        content = "".join(panel.get("options", {}).get("content", "")
                          for panel in home.get("panels", []))
        assert "<style" not in content.lower(), (
            "the home page carries a <style> block, which the panel's sanitizer strips; "
            "the cards would render unstyled"
        )
        assert 'style="' in content, "the cards carry no inline styling at all"

    def test_the_instructions_shall_print_the_address_people_copy(self, grafana_api):
        """The whole point of the page: a command that works when pasted unchanged.

        MCP_PATH is substituted at build time, but the host and scheme are not knowable
        then -- they come from the runtime SERVER_NAME and DEBUG -- so grafana's entrypoint
        rewrites @@MCP_URL@@ at container start. Both halves have to have happened.
        """
        dashboard = grafana_api.get(
            f"/api/dashboards/uid/{APP_NAME}-ai-assistant").json()["dashboard"]
        content = "".join(panel.get("options", {}).get("content", "")
                          for panel in dashboard.get("panels", []))
        assert "@@MCP_URL@@" not in content, (
            "the entrypoint did not substitute @@MCP_URL@@; the page still shows the "
            "placeholder instead of an address to copy"
        )
        assert "${" not in content, (
            f"an unsubstituted build-time token is left in the instructions: {content[:200]}"
        )
        # The address the tests themselves reach the stack under, so this asserts the
        # entrypoint derived it from the real root URL rather than a default.
        expected = f"{BASE_URL}/{MCP_PATH}/mcp"
        assert expected in content, (
            f"the instructions do not print {expected}; they say: "
            f"{[line for line in content.splitlines() if 'claude mcp add' in line]}"
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
