"""The SQLMesh analytics pipeline produces data for every example tenant, end to end.

run-tests.sh brings up a fresh stack whose database is seeded with the three example
tenants (postgresql/initdb/gf_0006_create_example_tenants.sql); the SQLMesh engine then
runs its first plan and backfills bronze -> silver -> gold. These tests read the result as
the read-only grafana role -- the actual consumer of gold -- and assert the harmonized
data made it all the way through.

The point of interest is project_c: its bronze layer is a polars Python model
(sqlmesh/models/bronze/project_c/issues_raw.py) rather than the SQL transform the other two
use, so a failure there (a missing polars dependency in the image, a broken transform, the
tenant left out of the silver union) would show up here as project_c missing from gold,
while project_a/project_b still pass.

The second point of interest is silver.issue_risk_history, the same history built two ways:
project_a's half by PostgreSQL with the @temporal_join macro, project_b's by DuckDB with a
native ASOF JOIN on the duckdb gateway. Both assertions run against PostgreSQL, which is
what the SQLMesh unit tests cannot cover -- those run on DuckDB, so SQL PostgreSQL rejects
would pass there and fail here, and a gateway that never wrote its table back into
PostgreSQL would show up only in this file.
"""
import subprocess
import time

import pytest
from conftest import GOLD_SCHEMA, SILVER_SCHEMA

# The example tenants seeded into a fresh stack. project_c is the polars Python-model one.
EXAMPLE_TENANTS = {"project_a", "project_b", "project_c"}


@pytest.fixture(scope="module", autouse=True)
def wait_for_backfill(grafana_db):
    """Block until the first SQLMesh plan has backfilled the tables asserted below.

    The session-wide wait_for_stack only waits for the sqlmesh *state* schema, which is
    created at the start of the first plan -- before bronze/silver/gold are backfilled. The
    analytics assertions below read finished tables, so wait specifically for them to appear
    and fill, rather than racing a slow first plan. Both are waited for: they are unrelated
    branches of the DAG, so either can finish first.
    """
    tables = (f"{GOLD_SCHEMA}.issue_metrics", f"{SILVER_SCHEMA}.issue_risk_history")
    deadline = time.time() + 180
    while True:
        try:
            with grafana_db.cursor() as cur:
                filled = 0
                for table in tables:
                    cur.execute("SELECT to_regclass(%s)", (table,))
                    if cur.fetchone()[0] is None:
                        break
                    cur.execute(f"SELECT count(*) FROM {table}")
                    if cur.fetchone()[0] == 0:
                        break
                    filled += 1
                if filled == len(tables):
                    return
        except Exception:
            pass
        if time.time() > deadline:
            pytest.fail(f"SQLMesh did not backfill {', '.join(tables)} in time")
        time.sleep(2)


def tenants_in(conn, table):
    """Return the distinct tenant_id values present in the given silver/gold table."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT tenant_id FROM {table}")
        return {row[0] for row in cur.fetchall()}


class TestAnalyticsPipeline:
    def test_gold_has_all_example_tenants(self, grafana_db):
        # The headline check: gold is the precomputed metrics layer dashboards read, and it
        # must carry a row for every tenant. Catches the polars bronze model (project_c)
        # silently dropping out of the pipeline.
        present = tenants_in(grafana_db, f"{GOLD_SCHEMA}.issue_metrics")
        assert EXAMPLE_TENANTS <= present, (
            f"{GOLD_SCHEMA}.issue_metrics is missing tenants: {EXAMPLE_TENANTS - present}"
        )

    def test_silver_has_all_example_tenants(self, grafana_db):
        # silver is where the per-tenant transforms are unioned together; confirming all
        # three appear here too pinpoints a regression to the union/transform rather than
        # to the gold aggregation if the gold check above fails.
        present = tenants_in(grafana_db, f"{SILVER_SCHEMA}.issues")
        assert EXAMPLE_TENANTS <= present, (
            f"{SILVER_SCHEMA}.issues is missing tenants: {EXAMPLE_TENANTS - present}"
        )

    def test_project_c_metrics_are_correct(self, grafana_db):
        # project_c's numbers come entirely from the polars transform decoding its seed
        # (seeds/project_c_issues.csv): five issues, the two "resolved" ones mapped to
        # closed, effort summed from the "weight" column (13+5+8+3+8). Asserting the exact
        # values proves the harmonization ran, not just that some rows arrived.
        with grafana_db.cursor() as cur:
            cur.execute(
                """
                SELECT total_issues, open_issues, closed_issues, total_effort
                FROM gold.issue_metrics
                WHERE tenant_id = 'project_c'
                """
            )
            row = cur.fetchone()

        assert row is not None, "project_c has no row in gold.issue_metrics"
        assert row == (5, 3, 2, 37), (
            "project_c metrics do not match the seed decoded by the polars transform"
        )

    def test_issue_risk_history_is_a_history_of_changes(self, grafana_db):
        # The @temporal_join example: project_a's issue history joined with the history of
        # the component each issue belongs to (sqlmesh/models/silver/project_a). It is the
        # one model here whose output is not a row per input row, so it is asserted whole,
        # against the seeds. Two component changes are deliberately absent -- C-PWR on the
        # 12th, while PA-1 is still on C-NAV, and C-PWR again on the 20th, which
        # reclassified it to what it already was -- while PA-4's reopening on the 14th, a
        # row identical to its own first row but for the timestamp, is deliberately there.
        with grafana_db.cursor() as cur:
            cur.execute(
                """
                SELECT issue_id, valid_from, state, effort, component_id, safety_class, owner
                FROM silver.issue_risk_history
                WHERE tenant_id = 'project_a'
                ORDER BY issue_id, valid_from
                """
            )
            rows = [(i, str(v), *rest) for i, v, *rest in cur.fetchall()]

        assert rows == [
            ("PA-1", "2026-06-10", "todo", 5, "C-NAV", "B", "Navigation"),
            ("PA-1", "2026-06-14", "in_progress", 5, "C-NAV", "B", "Navigation"),
            ("PA-1", "2026-06-16", "in_progress", 5, "C-NAV", "C", "Navigation"),
            ("PA-1", "2026-06-18", "in_progress", 8, "C-PWR", "D", "Power"),
            ("PA-1", "2026-06-22", "closed", 8, "C-PWR", "D", "Power"),
            ("PA-2", "2026-06-11", "todo", 3, None, None, None),
            ("PA-2", "2026-06-15", "closed", 3, None, None, None),
            ("PA-3", "2026-06-13", "todo", 2, "C-CAB", None, None),
            ("PA-3", "2026-06-17", "todo", 2, "C-CAB", "A", "Cabin"),
            ("PA-4", "2026-06-10", "todo", 1, "C-NAV", "B", "Navigation"),
            ("PA-4", "2026-06-12", "closed", 1, "C-NAV", "B", "Navigation"),
            ("PA-4", "2026-06-14", "todo", 1, "C-NAV", "B", "Navigation"),
            ("PA-4", "2026-06-16", "todo", 1, "C-NAV", "C", "Navigation"),
        ]

    def test_issue_risk_history_is_built_by_the_duckdb_gateway_too(self, grafana_db):
        # project_b's half of the same silver model is built by the same macro on the
        # DuckDB gateway (sqlmesh/config.py), which attaches this very database as its only
        # catalog: the engine is DuckDB, the storage is still PostgreSQL. So this assertion
        # is the proof that the gateway works end to end -- the rows are read back from
        # PostgreSQL, as the grafana role, from a table a DuckDB ASOF JOIN wrote.
        #
        # Two of A-DATA's changes are deliberately absent: the one on the 12th happens
        # while 2001 is still on A-API, and the one on the 20th reclassified it to the
        # class it already had.
        with grafana_db.cursor() as cur:
            cur.execute(
                """
                SELECT issue_id, valid_from, state, effort, component_id, safety_class, owner
                FROM silver.issue_risk_history
                WHERE tenant_id = 'project_b'
                ORDER BY issue_id, valid_from
                """
            )
            rows = [(i, str(v), *rest) for i, v, *rest in cur.fetchall()]

        assert rows == [
            ("2001", "2026-06-10", "todo", 2, "A-API", "B", "Platform"),
            ("2001", "2026-06-14", "todo", 8, "A-API", "B", "Platform"),
            ("2001", "2026-06-16", "todo", 8, "A-API", "C", "Platform"),
            ("2001", "2026-06-18", "todo", 8, "A-DATA", "D", "Data"),
            ("2001", "2026-06-22", "closed", 8, "A-DATA", "D", "Data"),
            ("2002", "2026-06-11", "todo", 5, None, None, None),
            ("2002", "2026-06-15", "closed", 5, None, None, None),
            ("2003", "2026-06-13", "todo", 2, "A-UI", None, None),
            ("2003", "2026-06-17", "todo", 2, "A-UI", "A", "Design"),
            ("2004", "2026-06-10", "todo", 2, "A-API", "B", "Platform"),
            ("2004", "2026-06-12", "closed", 2, "A-API", "B", "Platform"),
            ("2004", "2026-06-14", "todo", 2, "A-API", "B", "Platform"),
            ("2004", "2026-06-16", "todo", 2, "A-API", "C", "Platform"),
        ]

    def test_sqlmesh_unit_tests_pass(self):
        # The SQLMesh unit tests (sqlmesh/tests/*.yaml) state the awkward cases of
        # @temporal_join as input rows and expected output rows, which the seeded pipeline
        # above cannot: they run the models against fixtures instead of against the seeds,
        # one per gateway. They are run here, in the deployed container, because that is
        # where the engine and its dependencies are -- the suite itself has no SQLMesh
        # installation.
        result = subprocess.run(
            ["podman", "exec", "sqlmesh", "uv", "run", "--project", "/sqlmesh", "sqlmesh", "test"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_temporal_join_macro_agrees_across_gateways(self):
        # The macro emits a different join per gateway, so the one thing neither yaml test
        # can show is that the two say the same thing: they run different tenants over
        # different data. sqlmesh/tests/test_temporal_join.py renders both branches over
        # one fixture and compares them row for row, and runs in the container for the same
        # reason as the tests above.
        result = subprocess.run(
            ["podman", "exec", "sqlmesh", "uv", "run", "--project", "/sqlmesh",
             "python", "/sqlmesh/app/tests/test_temporal_join.py"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
