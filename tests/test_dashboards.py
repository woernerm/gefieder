"""The dashboards, end to end: defined in the models repository, drawn by the dashboards
service, shown by the admin panel.

What a fresh installation seeds -- the issue dashboard over the example models and the
server monitoring -- is what these read, through the proxy as a browser does. Each panel is
fetched on its own, as the page's htmx does, and its payload checked for the data the
query and its filters say it should hold.
"""
import json
import re

import pytest

from conftest import (
    CRUDMAN_PATH, DASHBOARDS, DASHBOARDS_DB_USER, SECRETS, SERVER_STATS_SCHEMA, podman,
)
from test_analytics import wait_for_backfill  # noqa: F401 -- the examples need the models.
from test_server_stats import run_collector

PANEL = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.DOTALL)


def panel(session, board, index, **filters):
    """The payload of one panel, as the page's htmx fetches it."""
    resp = session.get(f"{DASHBOARDS}{board}/{index}/", params=filters,
                       headers={"HX-Request": "true"})
    assert resp.status_code == 200, resp.text
    return json.loads(PANEL.search(resp.text).group(1))


def column(payload, name):
    """One column of a drawn chart's data, by name."""
    dataset = payload["option"]["dataset"]
    position = dataset["dimensions"].index(name)
    return [row[position] for row in dataset["source"]]


class TestTheList:
    def test_shall_offer_every_seeded_dashboard(self, admin_session):
        page = admin_session.get(DASHBOARDS).text

        assert f'href="{DASHBOARDS}issues/"' in page
        assert f'href="{DASHBOARDS}server/"' in page

    def test_shall_have_loaded_every_file_the_seed_holds(self, admin_session):
        """A file that fails to load is listed as not shown, with its error."""
        assert "Not shown" not in admin_session.get(DASHBOARDS).text

    def test_an_anonymous_visitor_shall_sign_in_first(self, http):
        resp = http.get(DASHBOARDS)

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(f"/{CRUDMAN_PATH}/login/")


class TestADashboard:
    def test_shall_offer_the_filters_its_queries_read(self, admin_session):
        page = admin_session.get(f"{DASHBOARDS}issues/").text

        assert '<select name="project">' in page
        assert '<select name="state">' in page
        # Filled from its own query: the projects the example models hold.
        assert "<option>project_a</option>" in page

    def test_shall_select_what_the_address_picked(self, admin_session):
        page = admin_session.get(f"{DASHBOARDS}issues/", params={"project": "project_b"}).text

        assert "<option selected>project_b</option>" in page

    def test_a_chart_shall_carry_every_project(self, admin_session):
        payload = panel(admin_session, "issues", 2)

        assert payload["kind"] == "echarts"
        assert set(column(payload, "project")) == {"project_a", "project_b", "project_c"}

    def test_a_filter_shall_narrow_every_panel_reading_it(self, admin_session):
        payload = panel(admin_session, "issues", 2, project="project_a")

        assert column(payload, "project") == ["project_a"]

    def test_a_click_shall_name_the_filter_it_sets(self, admin_session):
        assert panel(admin_session, "issues", 2)["click"] == "project"

    def test_one_query_shall_feed_differently_shaped_panels(self, admin_session):
        """The stat and the chart read the same query, the stat through a transform."""
        chart = panel(admin_session, "issues", 2)
        stat = panel(admin_session, "issues", 0)

        assert stat["kind"] == "stat"
        assert stat["value"] == sum(column(chart, "Open"))

    def test_a_query_in_long_form_shall_arrive_as_a_series_per_project(self, admin_session):
        payload = panel(admin_session, "issues", 5)

        assert {"project_a", "project_b", "project_c"} <= set(payload["option"]["dataset"]["dimensions"])

    def test_a_table_shall_carry_its_rows(self, admin_session):
        payload = panel(admin_session, "issues", 6, state="open")

        assert payload["kind"] == "table"
        state = payload["columns"].index("State")
        assert payload["rows"] and {row[state] for row in payload["rows"]} == {"open"}

    def test_a_panel_shall_download_for_excel(self, admin_session):
        resp = admin_session.get(f"{DASHBOARDS}issues/6.xlsx", params={"project": "project_a"})

        assert resp.status_code == 200
        assert resp.content[:2] == b"PK", "not an xlsx workbook"

    def test_a_dashboard_nobody_defined_shall_be_a_404(self, admin_session):
        assert admin_session.get(f"{DASHBOARDS}nonexistent/").status_code == 404


class TestServerMonitoring:
    """The dashboard that used to be Grafana's, over the server statistics."""

    @pytest.fixture(scope="class", autouse=True)
    def sampled(self):
        # Two samples, so the counters have a difference to draw.
        run_collector()
        run_collector()

    def test_shall_draw_every_panel(self, admin_session):
        page = admin_session.get(f"{DASHBOARDS}server/").text
        count = page.count('<section class="dashboards-panel')
        kinds = [panel(admin_session, "server", index)["kind"] for index in range(count)]

        assert count == 11
        assert "error" not in kinds, kinds

    def test_the_memory_shall_be_drawn_over_time(self, admin_session):
        payload = panel(admin_session, "server", 5)

        assert payload["unit"] == "bytes"
        assert column(payload, "Memory used")

    def test_the_time_range_shall_be_a_filter_of_its_own(self, admin_session):
        page = admin_session.get(f"{DASHBOARDS}server/").text

        assert '<select name="since">' in page
        assert "<option selected>6 hours</option>" in page

    def test_shall_refresh_itself(self, admin_session):
        assert "every 60s" in admin_session.get(f"{DASHBOARDS}server/").text


class TestTheService:
    """The dashboards run models-repository code, so they run apart from the admin panel."""

    def test_shall_connect_as_the_read_only_role(self):
        assert podman("exec", "dashboards", "printenv", "POSTGRES_USER").strip() == DASHBOARDS_DB_USER

    @pytest.mark.parametrize("secret", ["django_key", "crudman", "sqlmesh", "oidc_client"])
    def test_shall_hold_no_other_credential(self, secret):
        result = podman("exec", "dashboards", "sh", "-c",
                        f"test -e /run/secrets/{SECRETS[secret]} && echo held || echo absent")
        assert result.strip() == "absent"

    def test_shall_not_be_reachable_through_the_proxy(self, http):
        """Only on the pod's loopback: its answers are the admin panel's to hand out."""
        assert http.get("/dashboards/").status_code == 404

    def test_the_seeded_queries_shall_name_the_configured_schema(self):
        """The seed is rendered at build time; the schema is a build-time setting."""
        query = podman("exec", "crudman", "cat", "/seed/dashboards/queries/memory.sql")

        assert f"FROM {SERVER_STATS_SCHEMA}.host_sample" in query


class TestInANotebook:
    """A notebook draws a chart with the same two scripts, fetched by their plain names."""

    @pytest.mark.parametrize("script", ["dashboards/dashboards.js", "docs/echarts.min.js",
                                        "dashboards/dashboards.css", "dashboards/tabulator.min.js",
                                        "dashboards/tabulator.min.css"])
    def test_the_scripts_shall_be_served_by_name(self, http, script):
        assert http.get(f"/{CRUDMAN_PATH}/static/{script}").status_code == 200
