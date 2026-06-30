"""Server-statistics recording: the schema, the query stats and the host collector.

These cover the data-recording side that sizes a future server (CPU, RAM, temp/fast
storage, disk space, IOPS, throughput, egress) and finds queries worth an index. The
display is added later, so the tests assert that the data is collected, not how it looks.
"""
import hashlib
import os
import subprocess
import time
import uuid

import httpx
import pytest

from conftest import (
    BASE_URL, COLLECTOR, CRUDMAN_PATH, GRAFANA_PATH, SERVER_STATS_SCHEMA,
    SUPERUSER_NAME, VERIFY_TLS, denied,
)


# The tables the collector fills and the rollup the schema must expose.
SAMPLE_TABLES = ["host_sample", "query_sample", "table_sample", "host_hourly"]


def q(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()


class TestServerStatsSchema:
    """The server-statistics schema, tables and rollup function exist with grafana read."""

    def test_schema_shall_exist(self, admin_db):
        with admin_db.cursor() as cur:
            row = q(cur, "SELECT 1 FROM pg_namespace WHERE nspname = %s",
                    (SERVER_STATS_SCHEMA,))
            assert row is not None, f"schema {SERVER_STATS_SCHEMA} is missing"

    @pytest.mark.parametrize("table", SAMPLE_TABLES)
    def test_sample_tables_shall_exist(self, admin_db, table):
        with admin_db.cursor() as cur:
            row = q(cur,
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    (SERVER_STATS_SCHEMA, table))
            assert row is not None, f"table {SERVER_STATS_SCHEMA}.{table} is missing"

    def test_rollup_function_shall_exist(self, admin_db):
        with admin_db.cursor() as cur:
            row = q(cur,
                    "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = %s AND p.proname = 'rollup_and_prune'",
                    (SERVER_STATS_SCHEMA,))
            assert row is not None, "rollup_and_prune() is missing"


class TestQueryStatistics:
    """pg_stat_statements is loaded so the per-query optimisation data is available."""

    def test_pg_stat_statements_shall_be_installed(self, admin_db):
        with admin_db.cursor() as cur:
            row = q(cur, "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")
            assert row is not None, "pg_stat_statements extension is not installed"

    def test_pg_stat_statements_shall_be_preloaded(self, admin_db):
        # The view only works when the library is preloaded; selecting from it proves both
        # the preload and the extension registration took effect.
        with admin_db.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_stat_statements")
            assert cur.fetchone()[0] >= 0

    def test_duckdb_shall_still_be_preloaded(self, admin_db):
        # The preload line re-lists pg_duckdb; assert DuckDB is still loaded so the new
        # pg_stat_statements entry did not drop it.
        with admin_db.cursor() as cur:
            cur.execute("SHOW shared_preload_libraries")
            libs = cur.fetchone()[0]
        assert "pg_duckdb" in libs and "pg_stat_statements" in libs, libs


class TestGrafanaAccess:
    """grafana reads the server-statistics data (for the later dashboards) but cannot write."""

    def test_grafana_shall_read_the_host_samples(self, grafana_db):
        with grafana_db.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_sample')
            assert cur.fetchone()[0] >= 0

    def test_grafana_shall_not_write_the_host_samples(self, grafana_db):
        denied(grafana_db,
               f'INSERT INTO {SERVER_STATS_SCHEMA}.host_sample (cpu_usage_usec) VALUES (1)')


def run_collector():
    """Run the host collector once, asserting it exits cleanly.

    POSTGRES_USER lets it authenticate as the superuser inside the container; the schema
    name matches what the suite was told. RUNTIME_ENV=/dev/null skips the runtime.env
    lookup so the default interval applies. HOME/PATH are passed through because the
    collector resolves its state dir under HOME and runs podman from PATH.
    """
    if not COLLECTOR:
        pytest.skip("no collector path provided (GEFIEDER_COLLECTOR unset)")
    proc = subprocess.run(
        [COLLECTOR],
        env={"POSTGRES_USER": SUPERUSER_NAME, "SERVER_STATS_SCHEMA": SERVER_STATS_SCHEMA,
             "RUNTIME_ENV": "/dev/null", "HOME": os.environ["HOME"],
             "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"collector failed: {proc.stderr}\n{proc.stdout}"


# The collector run is the slow part (it execs into the container and probes the host), so
# run it once per module and let several tests assert on the single sample it produced.
@pytest.fixture(scope="module")
def collected(admin_db):
    """Run the host collector once and return the timestamp of the sample it inserted."""
    with admin_db.cursor() as cur:
        before = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_sample')[0]
    run_collector()
    with admin_db.cursor() as cur:
        after = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_sample')[0]
    assert after == before + 1, "the collector did not insert exactly one host sample"
    return after


class TestHostCollector:
    """A real collector run records the host resource counters used for sizing."""

    def test_a_run_shall_insert_a_host_sample(self, collected):
        # The fixture already asserts exactly one new row landed; reaching here proves it.
        assert collected >= 1

    def test_the_sample_shall_carry_the_cpu_and_memory_gauges(self, admin_db, collected):
        # CPU usage, host core count and pod memory come from the cgroup, which is present
        # for a rootless-podman pod, so these must be non-null on a successful run.
        with admin_db.cursor() as cur:
            cpu, nproc, mem = q(cur,
                f"SELECT cpu_usage_usec, host_nproc, mem_current_bytes "
                f"FROM {SERVER_STATS_SCHEMA}.host_sample ORDER BY sampled_at DESC LIMIT 1")
        assert cpu is not None and cpu > 0, "cpu_usage_usec not recorded"
        assert nproc is not None and nproc >= 1, "host_nproc not recorded"
        assert mem is not None and mem > 0, "mem_current_bytes not recorded"

    def test_the_sample_shall_carry_the_network_egress_counter(self, admin_db, collected):
        # The monthly sum of tx deltas is the outgoing-traffic figure, so the counter must
        # be present and monotonic-looking (non-negative).
        with admin_db.cursor() as cur:
            tx = q(cur,
                f"SELECT net_tx_bytes FROM {SERVER_STATS_SCHEMA}.host_sample "
                f"ORDER BY sampled_at DESC LIMIT 1")[0]
        assert tx is not None and tx >= 0, "net_tx_bytes not recorded"

    def test_a_run_shall_snapshot_query_and_table_statistics(self, admin_db, collected):
        # The same run also snapshots pg_stat_statements and pg_stat_user_tables; both
        # views are non-empty on a live stack, so each snapshot must have rows.
        with admin_db.cursor() as cur:
            queries = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.query_sample')[0]
            tables = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.table_sample')[0]
        assert queries > 0, "no query statistics were snapshotted"
        assert tables > 0, "no table statistics were snapshotted"


class TestRollup:
    """The rollup folds raw samples into the long-term hourly table used for sizing."""

    def test_rollup_shall_populate_the_hourly_table(self, admin_db, collected):
        # The collector calls rollup_and_prune() each tick, so after a run the current hour
        # bucket exists. Call it again directly to prove it is idempotent and re-runnable.
        with admin_db.cursor() as cur:
            cur.execute(f"SELECT {SERVER_STATS_SCHEMA}.rollup_and_prune()")
            rows = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_hourly')[0]
        assert rows >= 1, "rollup did not produce an hourly bucket"

    def test_rollup_shall_be_idempotent(self, admin_db, collected):
        # Running it twice must not change the bucket count (ON CONFLICT updates in place).
        with admin_db.cursor() as cur:
            cur.execute(f"SELECT {SERVER_STATS_SCHEMA}.rollup_and_prune()")
            first = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_hourly')[0]
            cur.execute(f"SELECT {SERVER_STATS_SCHEMA}.rollup_and_prune()")
            second = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_hourly')[0]
        assert first == second, "rollup is not idempotent"


# A unique dashboard uid and session cookie per test run, so the assertions match exactly
# the visits this test generated and never a leftover row from earlier traffic.
VISIT_UID = uuid.uuid4().hex[:12]
VISIT_COOKIE = "sess-" + uuid.uuid4().hex


@pytest.fixture(scope="module")
def visits(admin_db):
    """Generate page visits through the proxy, then drain them with one collector run.

    The proxy logs the request regardless of how Grafana/crudman answer it (even a redirect
    to login), so the pipeline can be tested without authenticating. Noise requests (API,
    assets, a POST) are sent too and must NOT appear, proving the nginx filter holds.
    """
    nav_dashboard = f"/{GRAFANA_PATH}/d/{VISIT_UID}/probe"
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, follow_redirects=False,
                      timeout=10, cookies={"grafana_session": VISIT_COOKIE}) as c:
        c.get(nav_dashboard)                          # grafana dashboard nav -> logged
        c.get(f"/{GRAFANA_PATH}/api/dashboards/uid/{VISIT_UID}")  # API -> skipped
        c.get(f"/{GRAFANA_PATH}/public/build/app.js")            # asset -> skipped
        c.get(f"/{CRUDMAN_PATH}/")                     # crudman page nav -> logged
        c.post(f"/{CRUDMAN_PATH}/login/")              # POST -> skipped

    # nginx buffers the access log; a tiny pause lets the lines flush before the collector
    # reads the file. The collector then drains visits.log into dashboard_visit.
    time.sleep(1)
    run_collector()
    return nav_dashboard


class TestDashboardVisits:
    """Page navigations through the proxy are recorded, with noise filtered out."""

    def test_a_grafana_dashboard_visit_shall_be_recorded(self, admin_db, visits):
        with admin_db.cursor() as cur:
            row = q(cur,
                f"SELECT app, dashboard_uid FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE dashboard_uid = %s", (VISIT_UID,))
        assert row is not None, "the grafana dashboard visit was not recorded"
        assert row[0] == "grafana" and row[1] == VISIT_UID

    def test_a_crudman_page_visit_shall_be_recorded(self, admin_db, visits):
        # crudman views ride the same pipeline; assert at least one landed for this app.
        with admin_db.cursor() as cur:
            cnt = q(cur,
                f"SELECT count(*) FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE app = 'crudman'")[0]
        assert cnt >= 1, "no crudman page visit was recorded"

    def test_api_and_asset_requests_shall_not_be_recorded(self, admin_db, visits):
        # The noise requests share the unique uid in their path but are API/asset/POST, so
        # the only row carrying this uid must be the one dashboard navigation.
        with admin_db.cursor() as cur:
            paths = [r[0] for r in _all(cur,
                f"SELECT url_path FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE url_path LIKE %s", (f"%{VISIT_UID}%",))]
        assert paths == [f"/{GRAFANA_PATH}/d/{VISIT_UID}/probe"], \
            f"noise requests leaked into visits: {paths}"

    def test_the_session_cookie_shall_be_hashed_not_stored(self, admin_db, visits):
        # The raw cookie must never be stored; the session_hash is its md5, so it equals
        # md5(cookie) and never contains the cookie value itself.
        expected = hashlib.md5(VISIT_COOKIE.encode()).hexdigest()
        with admin_db.cursor() as cur:
            row = q(cur,
                f"SELECT session_hash FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE dashboard_uid = %s", (VISIT_UID,))
            leaked = q(cur,
                f"SELECT count(*) FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE session_hash LIKE %s", (f"%{VISIT_COOKIE}%",))[0]
        assert row[0] == expected, "session_hash is not md5(cookie)"
        assert leaked == 0, "the raw session cookie leaked into the database"

    def test_grafana_shall_read_but_not_write_visits(self, grafana_db):
        with grafana_db.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {SERVER_STATS_SCHEMA}.dashboard_visit")
            assert cur.fetchone()[0] >= 0
        denied(grafana_db,
               f"INSERT INTO {SERVER_STATS_SCHEMA}.dashboard_visit (app) VALUES ('x')")


def _all(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()
