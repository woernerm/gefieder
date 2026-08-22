"""The chart panels: what a stored SQL statement may and may not do.

A panel's query is a row in the database rather than code in the repository, so the
guards around it are the point of this file. They are two, and both belong to the
database:

* the panels connection authenticates as the analytics role, which holds no write grant
  on anything it can read, and
* each statement runs in a read-only transaction with a statement timeout.

Neither alone is enough -- a read-only transaction can be lifted from inside by
``SET TRANSACTION READ WRITE`` -- which is why the write attempts below are made both
ways round.
"""

import json

import pytest

from conftest import BASE_URL, CRUDMAN_PATH, SUPERUSER_NAME, SUPERUSER_PASSWORD, VERIFY_TLS

PANEL_SLUG = "test-open-issues"
PANEL_SQL = (
    "SELECT tenant_id, open_issues, closed_issues "
    "FROM gold.issue_metrics ORDER BY tenant_id"
)


def crudman(script):
    """Run a snippet inside the crudman container's Django shell and return its stdout.

    Args:
        script: The Python to run.

    Returns:
        The command's standard output.
    """
    import subprocess

    return subprocess.run(
        ["podman", "exec", "crudman", "uv", "run", "--project", "/crudman",
         "python", "manage.py", "shell", "-c", script],
        capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture(scope="module")
def panel():
    """A stored panel to query, removed again afterwards."""
    crudman(
        "from panels.models import Panel;"
        f"Panel.objects.update_or_create(slug='{PANEL_SLUG}',"
        f" defaults=dict(title='Test panel', sql=\"{PANEL_SQL}\", chart_type='bar'))"
    )
    yield PANEL_SLUG
    crudman(
        "from panels.models import Panel;"
        f"Panel.objects.filter(slug='{PANEL_SLUG}').delete()"
    )


def run_sql(sql):
    """Run a statement through the panel executor and report what happened.

    Args:
        sql: The statement to run as a panel would.

    Returns:
        "ok" followed by the rows, or "blocked" followed by the database's complaint.
    """
    script = (
        "from panels.query import run, PanelQueryError\n"
        "try:\n"
        f"    cols, rows = run({sql!r})\n"
        "    print('ok', rows)\n"
        "except PanelQueryError as error:\n"
        "    print('blocked', error)\n"
    )
    return crudman(script).strip().splitlines()[-1]


class TestQueryGuards:
    """What the analytics role and the read-only transaction refuse."""

    def test_a_panel_shall_read_the_gold_layer(self, panel):
        assert run_sql(PANEL_SQL).startswith("ok")

    @pytest.mark.parametrize("statement", [
        "CREATE TABLE public.panel_probe (id int)",
        "INSERT INTO gold.issue_metrics VALUES ('x', 1, 1, 1, 1)",
        "UPDATE gold.issue_metrics SET open_issues = 0",
        "DROP TABLE IF EXISTS gold.issue_metrics",
    ])
    def test_a_panel_shall_not_write(self, statement):
        assert run_sql(statement).startswith("blocked")

    def test_lifting_the_read_only_mode_shall_not_grant_a_write(self):
        """The escape exists; the role's missing grant is what makes it worthless."""
        assert run_sql(
            "SET TRANSACTION READ WRITE; CREATE TABLE public.panel_probe (id int)"
        ).startswith("blocked")

    def test_a_panel_shall_not_read_the_django_credential_tables(self):
        """The analytics role sees the model tables but not auth_user or the sessions."""
        assert run_sql("SELECT password FROM crudman.auth_user").startswith("blocked")
        assert run_sql("SELECT session_data FROM crudman.django_session").startswith("blocked")

    def test_a_panel_shall_not_read_the_sqlmesh_internals(self):
        assert run_sql("SELECT * FROM sqlmesh._snapshots").startswith("blocked")

    def test_a_statement_that_fails_shall_not_break_the_connection(self):
        """The rollback has to leave the pooled connection usable for the next panel."""
        run_sql("SELECT * FROM does.not_exist")
        assert run_sql("SELECT 1").startswith("ok")

    def test_a_long_statement_shall_be_cancelled(self):
        assert "timeout" in run_sql("SELECT pg_sleep(120)").lower()


class TestPanelEndpoint:
    """The fragment each placeholder fetches for itself."""

    def test_the_endpoint_shall_require_a_session(self, http, panel):
        resp = http.get(f"/{CRUDMAN_PATH}/panels/{panel}/data/")
        assert resp.status_code == 302
        assert f"/{CRUDMAN_PATH}/login/" in resp.headers["location"]

    def test_the_endpoint_shall_render_the_chart_options(self, panel):
        """A signed-in administrator gets the div panels.init.js turns into a chart."""
        import httpx

        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=True, timeout=30) as client:
            login = f"/{CRUDMAN_PATH}/login/"
            client.get(login)
            client.post(login, data={
                "username": SUPERUSER_NAME,
                "password": SUPERUSER_PASSWORD,
                "csrfmiddlewaretoken": client.cookies.get("csrftoken", ""),
                "next": f"/{CRUDMAN_PATH}/",
            }, headers={"Referer": f"{BASE_URL}{login}"})

            resp = client.get(f"/{CRUDMAN_PATH}/panels/{panel}/data/")

        assert resp.status_code == 200
        assert "echarts-panel" in resp.text

        # The options have to survive template escaping as usable JSON, which is what
        # the browser hands to ECharts.
        import re
        raw = re.search(r'data-options="(.*?)"\s', resp.text, re.S).group(1)
        options = json.loads(raw.replace("&quot;", '"').replace("&#x27;", "'"))
        assert [series["name"] for series in options["series"]] == [
            "open_issues", "closed_issues"
        ]
        assert options["xAxis"]["data"] == ["project_a", "project_b", "project_c"]


class TestAssets:
    """ECharts is vendored, so a machine without internet access still draws charts."""

    @pytest.mark.parametrize("asset", ["echarts.min.js", "panels.init.js"])
    def test_the_chart_assets_shall_be_served(self, http_follow, asset):
        resp = http_follow.get(f"/{CRUDMAN_PATH}/static/panels/{asset}")
        assert resp.status_code == 200
        assert len(resp.content) > 1000
