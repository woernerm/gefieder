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

PANEL_TITLE = "Test panel"
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


def crudman_manage(*command):
    """Run a management command inside the crudman container and return its output.

    Args:
        *command: The command and its arguments, e.g. ``"check_queries"``.

    Returns:
        The command's standard output and standard error together, since a failing check
        writes to the latter.
    """
    import subprocess

    result = subprocess.run(
        ["podman", "exec", "crudman", "uv", "run", "--project", "/crudman",
         "python", "manage.py", *command],
        capture_output=True, text=True,
    )
    return result.stdout + result.stderr


@pytest.fixture(scope="module")
def panel():
    """A stored query, chart and panel to fetch, removed again afterwards.

    The chart names ``${placeholder}`` tokens rather than columns and the panel binds
    them, which is the arrangement the endpoint has to resolve. The panel is yielded by
    primary key, which is what the endpoint takes.
    """
    output = crudman(
        "from analytics.models import Chart, Panel, Query\n"
        "q, _ = Query.objects.update_or_create(title='Test query',"
        f" defaults=dict(sql=\"{PANEL_SQL}\"))\n"
        "c, _ = Chart.objects.update_or_create(title='Test chart',"
        " defaults=dict(options={'series': [{'type': 'bar',"
        " 'encode': {'x': '${category}', 'y': '${measure}'}}]}))\n"
        f"p, _ = Panel.objects.update_or_create(title='{PANEL_TITLE}',"
        " defaults=dict(query=q, chart=c,"
        " bindings={'category': 'tenant_id', 'measure': 'open_issues'}))\n"
        "print('PANEL_PK', p.pk)\n"
    )
    pk = next(
        line.split()[1] for line in output.splitlines() if line.startswith("PANEL_PK ")
    )
    yield pk
    crudman(
        "from analytics.models import Chart, Panel, Query\n"
        f"Panel.objects.filter(title='{PANEL_TITLE}').delete()\n"
        "Query.objects.filter(title='Test query').delete()\n"
        "Chart.objects.filter(title='Test chart').delete()\n"
    )


def run_sql(sql):
    """Run a statement through the panel executor and report what happened.

    Args:
        sql: The statement to run as a panel would.

    Returns:
        "ok" followed by the rows, or "blocked" followed by the database's complaint.
    """
    script = (
        "from analytics.query import run, PanelQueryError\n"
        "try:\n"
        f"    cols, rows = run({sql!r}, {{}})\n"
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
        resp = http.get(f"/{CRUDMAN_PATH}/analytics/{panel}/data/")
        assert resp.status_code == 302
        assert f"/{CRUDMAN_PATH}/login/" in resp.headers["location"]

    def test_the_endpoint_shall_render_the_chart_options(self, panel):
        """A signed-in administrator gets the div analytics.init.js turns into a chart."""
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

            resp = client.get(f"/{CRUDMAN_PATH}/analytics/{panel}/data/")

        assert resp.status_code == 200
        assert "echarts-panel" in resp.text

        # The options have to survive template escaping as usable JSON, which is what
        # the browser hands to ECharts.
        import re
        raw = re.search(r'data-options="(.*?)"\s', resp.text, re.S).group(1)
        options = json.loads(raw.replace("&quot;", '"').replace("&#x27;", "'"))
        # The result reaches the browser as the "query" stage of the dataset chain, and
        # the series reads a stage by id rather than counting indices.
        stages = {stage["id"]: stage for stage in options["dataset"]}
        assert stages["query"]["dimensions"] == [
            "tenant_id", "open_issues", "closed_issues"
        ]
        assert [row[0] for row in stages["query"]["source"]] == [
            "project_a", "project_b", "project_c"
        ]

        # The chart stored ${category} and ${measure}; the panel's bindings are what
        # turned them into columns.
        assert options["series"][0]["encode"] == {
            "x": "tenant_id", "y": "open_issues"
        }
        assert options["series"][0]["datasetId"] in stages


class TestAssets:
    """ECharts is vendored, so a machine without internet access still draws charts."""

    @pytest.mark.parametrize("asset", ["echarts.min.js", "ecSimpleTransform.js",
                                       "analytics.init.js"])
    def test_the_chart_assets_shall_be_served(self, http_follow, asset):
        resp = http_follow.get(f"/{CRUDMAN_PATH}/static/analytics/{asset}")
        assert resp.status_code == 200
        assert len(resp.content) > 1000


class TestExamplePanels:
    """The examples a fresh system starts with have to work against the real data."""

    def test_every_example_shall_render_with_the_columns_it_binds(self):
        """A binding naming a column the query does not return draws an empty chart.

        Only the database can say which columns a statement returns, which is why this
        lives here rather than in the app's own tests.
        """
        script = (
            "from analytics.models import Panel\n"
            "from analytics.query import run\n"
            "from analytics.transforms import columns_after\n"
            "for p in Panel.objects.filter(dashboard__name='home'):\n"
            "    cols, rows = run(p.query.sql, p.resolved_parameters)\n"
            "    available = set(columns_after(p.transforms, cols))\n"
            "    bound = set()\n"
            "    for value in (p.bindings or {}).values():\n"
            "        bound.update(value if isinstance(value, list) else [value])\n"
            "    missing = bound - available\n"
            "    print(p.title, 'MISSING' if missing else 'ok', sorted(missing))\n"
        )
        output = crudman(script)

        assert "MISSING" not in output, output
        assert "ok" in output, "no example panels were found at all"

    def test_every_example_query_shall_pass_its_own_checks(self):
        """The command a pipeline runs: a renamed gold column has to fail here."""
        output = crudman_manage("check_queries")

        assert "FAIL" not in output, output

    def test_every_signature_shall_match_what_the_query_returns(self):
        """The signature drives the binding dropdown, so a wrong one misleads an author.

        Reading ``columns`` rather than ``signature`` is the point: the examples are
        written at post_migrate, before SQLMesh has built silver and gold, so their first
        probe cannot succeed. Asking for the columns is what establishes them.
        """
        script = (
            "from analytics.models import Query\n"
            "from analytics.query import run\n"
            "for q in Query.objects.all():\n"
            "    cols, _ = run(q.sql, q.parameter_defaults or {})\n"
            "    print(q.title, 'ok' if q.columns == list(cols) else 'WRONG')\n"
        )
        output = crudman(script)

        assert "WRONG" not in output, output

    def test_a_query_two_panels_share_shall_run_once(self):
        """The reuse the split exists for has to cost one execution, not two.

        Both example panels read example-issues-by-state, so drawing the dashboard must
        reach the database once for it. Counted from the statement log the analytics role
        leaves behind rather than from the cache, which would only prove itself.
        """
        script = (
            "from analytics.models import Panel\n"
            "from analytics.query import run_shared, RESULT_CACHE\n"
            "from django.core.cache import caches\n"
            "caches[RESULT_CACHE].clear()\n"
            "calls = []\n"
            "import analytics.query as q\n"
            "real = q.run\n"
            "q.run = lambda s, v: calls.append(s) or real(s, v)\n"
            "panels = Panel.objects.filter("
            "query__title='Issues by tenant and state')\n"
            "for p in panels:\n"
            "    run_shared(p.query.sql, p.resolved_parameters)\n"
            "q.run = real\n"
            "print('panels', panels.count(), 'executions', len(calls))\n"
        )
        output = crudman(script)

        assert "panels 2 executions 1" in output, output

    def test_a_healed_signature_shall_be_written_back(self):
        """Healing once is the point; every panel form after it reads the stored row."""
        script = (
            "from analytics.models import Query\n"
            "q = Query.objects.first()\n"
            "q.columns\n"
            "print(q.title, 'stored' if Query.objects.get(pk=q.pk).signature else 'EMPTY')\n"
        )
        output = crudman(script)

        assert "EMPTY" not in output, output
