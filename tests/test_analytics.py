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
"""
import time

import pytest

# The example tenants seeded into a fresh stack. project_c is the polars Python-model one.
EXAMPLE_TENANTS = {"project_a", "project_b", "project_c"}


@pytest.fixture(scope="module", autouse=True)
def wait_for_backfill(grafana_db):
    """Block until the first SQLMesh plan has backfilled gold.

    The session-wide wait_for_stack only waits for the sqlmesh *state* schema, which is
    created at the start of the first plan -- before bronze/silver/gold are backfilled. The
    analytics assertions below read the finished gold table, so wait specifically for it to
    appear and fill, rather than racing a slow first plan.
    """
    deadline = time.time() + 180
    while True:
        try:
            with grafana_db.cursor() as cur:
                cur.execute("SELECT to_regclass('gold.issue_metrics')")
                exists = cur.fetchone()[0] is not None
                if exists:
                    cur.execute("SELECT count(*) FROM gold.issue_metrics")
                    if cur.fetchone()[0] > 0:
                        return
        except Exception:
            pass
        if time.time() > deadline:
            pytest.fail("SQLMesh did not backfill gold.issue_metrics in time")
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
        present = tenants_in(grafana_db, "gold.issue_metrics")
        assert EXAMPLE_TENANTS <= present, (
            f"gold.issue_metrics is missing tenants: {EXAMPLE_TENANTS - present}"
        )

    def test_silver_has_all_example_tenants(self, grafana_db):
        # silver is where the per-tenant transforms are unioned together; confirming all
        # three appear here too pinpoints a regression to the union/transform rather than
        # to the gold aggregation if the gold check above fails.
        present = tenants_in(grafana_db, "silver.issues")
        assert EXAMPLE_TENANTS <= present, (
            f"silver.issues is missing tenants: {EXAMPLE_TENANTS - present}"
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
