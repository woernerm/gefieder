"""Server-statistics recording: the schema, the query stats and the host collector.

These cover the recording side that sizes a future server (CPU, RAM, storage, IOPS,
throughput, egress) and finds queries worth an index. They assert that the data is
collected, not how it looks; the dashboard displaying it is checked in test_grafana.py.
"""
import hashlib
import os
import shlex
import subprocess
import time
import uuid

import httpx
import pytest

from conftest import (
    APP_NAME, BASE_URL, COLLECTOR, CRUDMAN_PATH, GRAFANA_PATH, SERVER_STATS_SCHEMA,
    SUPERUSER_NAME, VERIFY_TLS, denied,
)


# The tables the collector fills and the rollup the schema must expose.
SAMPLE_TABLES = ["host_sample", "query_sample", "query_dim", "table_sample",
                 "host_hourly"]


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
        # Selecting from the view proves both the preload and the registration.
        with admin_db.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_stat_statements")
            assert cur.fetchone()[0] >= 0

    def test_duckdb_shall_still_be_preloaded(self, admin_db):
        # The preload line re-lists pg_duckdb, so check it survived.
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


def run_collector(**extra_env):
    """Run the host collector once, asserting it exits cleanly.

    POSTGRES_USER authenticates as the superuser inside the container; HOME and PATH are
    where the collector resolves its state dir and finds podman. extra_env overrides a
    variable for one run.

    Supplying the variables here means a unit file that omits one goes unnoticed; that is
    what TestCollectorUnit below is for.
    """
    if not COLLECTOR:
        pytest.skip("no collector path provided (TEST_COLLECTOR unset)")
    env = {"POSTGRES_USER": SUPERUSER_NAME, "SERVER_STATS_SCHEMA": SERVER_STATS_SCHEMA,
           "APP_NAME": APP_NAME, "HOME": os.environ["HOME"],
           "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    env.update(extra_env)
    proc = subprocess.run([COLLECTOR], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, f"collector failed: {proc.stderr}\n{proc.stdout}"


def unit_environment(unit):
    """The environment systemd resolved for a unit, as a dict.

    `systemctl show -p Environment` prints the assignments on one line, quoted where
    needed -- exactly shlex's syntax.
    """
    out = subprocess.run(
        ["systemctl", "--user", "show", "-p", "Environment", "--value", unit],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return dict(item.split("=", 1) for item in shlex.split(out) if "=" in item)


class TestCollectorUnit:
    """The collector as the deployment invokes it: through its systemd unit.

    The deployed superuser name comes from SUPERUSER_NAME and the schema is baked into the
    PostgreSQL image from SERVER_STATS_SCHEMA. A unit that passes neither leaves a
    deployment that changed either one connecting as a role that does not exist, or writing
    to a schema never created — silently, since a failed oneshot only reaches the journal.
    """

    def test_the_unit_shall_pass_the_configured_user_and_schema(self):
        env = unit_environment("server-stats.service")
        assert env.get("POSTGRES_USER") == SUPERUSER_NAME, (
            "server-stats.service does not pass POSTGRES_USER, so the collector falls "
            f"back to 'admin' instead of {SUPERUSER_NAME!r}"
        )
        assert env.get("SERVER_STATS_SCHEMA") == SERVER_STATS_SCHEMA, (
            "server-stats.service does not pass SERVER_STATS_SCHEMA, so the collector "
            f"falls back to 'server_stats' instead of {SERVER_STATS_SCHEMA!r}"
        )

    def test_starting_the_unit_shall_take_a_sample(self, admin_db):
        # Through systemd, not the script: a oneshot start blocks until the run finishes
        # and fails on a non-zero exit, covering ExecStart and the environment together.
        with admin_db.cursor() as cur:
            before = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_sample')[0]
        proc = subprocess.run(
            ["systemctl", "--user", "start", "server-stats.service"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            f"server-stats.service failed to run: {proc.stderr}\n"
            + subprocess.run(
                ["journalctl", "--user", "-u", "server-stats.service", "-n", "20",
                 "--no-pager"], capture_output=True, text=True).stdout
        )
        with admin_db.cursor() as cur:
            after = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_sample')[0]
        assert after > before, "the unit ran but inserted no host sample"


# The collector run is the slow part, so it runs once per module and several tests assert
# on the single sample it produced.
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


@pytest.fixture(scope="module")
def sized(admin_db):
    """Run the collector once with the disk-size probe forced on.

    The sizes are probed only every SERVER_STATS_DISK_PROBE_SECONDS, so a plain run may
    skip them; 0 makes this run take them whenever the previous probe happened.
    """
    run_collector(SERVER_STATS_DISK_PROBE_SECONDS="0")


@pytest.fixture(scope="module")
def probed(admin_db):
    """Run the collector once with the query/table snapshot forced on.

    That snapshot has its own slow sub-cadence (SERVER_STATS_QUERY_PROBE_SECONDS), so a
    plain run usually skips it; 0 makes this run take it.
    """
    run_collector(SERVER_STATS_QUERY_PROBE_SECONDS="0")


class TestHostCollector:
    """A real collector run records the host resource counters used for sizing."""

    def test_a_run_shall_insert_a_host_sample(self, collected):
        # The fixture asserts exactly one new row landed; reaching here proves it.
        assert collected >= 1

    def test_the_sample_shall_carry_the_cpu_and_memory_gauges(self, admin_db, collected):
        # These come from the cgroup, which a rootless-podman pod always has.
        with admin_db.cursor() as cur:
            cpu, nproc, mem = q(cur,
                f"SELECT cpu_usage_usec, host_nproc, mem_current_bytes "
                f"FROM {SERVER_STATS_SCHEMA}.host_sample ORDER BY sampled_at DESC LIMIT 1")
        assert cpu is not None and cpu > 0, "cpu_usage_usec not recorded"
        assert nproc is not None and nproc >= 1, "host_nproc not recorded"
        assert mem is not None and mem > 0, "mem_current_bytes not recorded"

    def test_the_sample_shall_carry_the_network_egress_counter(self, admin_db, collected):
        # The monthly sum of tx deltas is the outgoing-traffic figure.
        with admin_db.cursor() as cur:
            tx = q(cur,
                f"SELECT net_tx_bytes FROM {SERVER_STATS_SCHEMA}.host_sample "
                f"ORDER BY sampled_at DESC LIMIT 1")[0]
        assert tx is not None and tx >= 0, "net_tx_bytes not recorded"

    def test_a_run_shall_snapshot_query_and_table_statistics(self, admin_db, probed):
        # Both views are non-empty on a live stack, so each snapshot must have rows.
        with admin_db.cursor() as cur:
            queries = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.query_sample')[0]
            tables = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.table_sample')[0]
        assert queries > 0, "no query statistics were snapshotted"
        assert tables > 0, "no table statistics were snapshotted"

    def test_the_statement_text_shall_be_stored_once_per_query(self, admin_db, probed):
        # The text lives in query_dim, keyed by queryid, so it is written once however
        # many samples a statement accumulates.
        with admin_db.cursor() as cur:
            texts = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.query_dim')[0]
            orphans = q(cur,
                f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.query_sample s '
                f'LEFT JOIN {SERVER_STATS_SCHEMA}.query_dim d USING (queryid) '
                f'WHERE d.queryid IS NULL')[0]
        assert texts > 0, "no statement texts were recorded"
        assert orphans == 0, "query_sample rows without a query_dim text"

    def test_a_repeated_snapshot_shall_not_restore_unchanged_rows(self, admin_db, probed):
        # Storing only what changed keeps the table small: a second snapshot may add the
        # statements this test ran, but not the thousands whose counters stood still.
        with admin_db.cursor() as cur:
            before = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.query_sample')[0]
        run_collector(SERVER_STATS_QUERY_PROBE_SECONDS="0")
        with admin_db.cursor() as cur:
            after = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.query_sample')[0]
            distinct = q(cur,
                f'SELECT count(DISTINCT queryid) FROM {SERVER_STATS_SCHEMA}.query_sample')[0]
        assert after - before < distinct, (
            f"second snapshot added {after - before} rows for {distinct} known statements; "
            "unchanged rows are being stored again"
        )

    # The counters a run must fill on any supported host, from the cgroup CPU/memory files
    # and the host network interface. The io_* columns are excluded: they need the cgroup
    # io controller delegated to the user slice, which some kernels do not provide.
    PER_SAMPLE_COLUMNS = [
        "cpu_usage_usec", "host_nproc",
        "mem_current_bytes", "mem_peak_bytes", "host_mem_total_bytes",
        "net_tx_bytes", "net_rx_bytes",
    ]

    @pytest.mark.parametrize("column", PER_SAMPLE_COLUMNS)
    def test_the_sample_shall_carry_every_non_io_counter(self, admin_db, collected, column):
        # On the row this run inserted, so data really is collected rather than the
        # sample being empty.
        with admin_db.cursor() as cur:
            value = q(cur,
                f"SELECT {column} FROM {SERVER_STATS_SCHEMA}.host_sample "
                f"ORDER BY sampled_at DESC LIMIT 1")[0]
        assert value is not None, f"{column} was not recorded"
        assert value >= 0, f"{column} is negative ({value})"

    def test_the_disk_and_volume_sizes_shall_be_collected(self, admin_db, sized):
        # The disk-space sizing inputs, probed on a slower sub-cadence that the `sized`
        # fixture forces on. Asserted over the whole table, so it does not matter which
        # row got the probe. Unlike IOPS this needs no io controller.
        with admin_db.cursor() as cur:
            db_size, vol_size, temp_size = q(cur,
                f"SELECT max(db_size_bytes), max(volume_size_bytes), max(temp_size_bytes) "
                f"FROM {SERVER_STATS_SCHEMA}.host_sample")
        assert db_size is not None and db_size > 0, "db_size_bytes never collected"
        assert vol_size is not None and vol_size > 0, "volume_size_bytes never collected"
        # Temp/spill may legitimately be 0, so only require non-null.
        assert temp_size is not None, "temp_size_bytes never collected"


class TestRollup:
    """The rollup folds raw samples into the long-term hourly table used for sizing."""

    def test_rollup_shall_populate_the_hourly_table(self, admin_db, collected):
        # The collector calls rollup_and_prune() each tick, so the current hour bucket
        # exists; calling it again proves it idempotent.
        with admin_db.cursor() as cur:
            cur.execute(f"SELECT {SERVER_STATS_SCHEMA}.rollup_and_prune()")
            rows = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_hourly')[0]
        assert rows >= 1, "rollup did not produce an hourly bucket"

    def test_rollup_shall_be_idempotent(self, admin_db, collected):
        # ON CONFLICT updates in place, so the bucket count must not change.
        with admin_db.cursor() as cur:
            cur.execute(f"SELECT {SERVER_STATS_SCHEMA}.rollup_and_prune()")
            first = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_hourly')[0]
            cur.execute(f"SELECT {SERVER_STATS_SCHEMA}.rollup_and_prune()")
            second = q(cur, f'SELECT count(*) FROM {SERVER_STATS_SCHEMA}.host_hourly')[0]
        assert first == second, "rollup is not idempotent"


# Unique per test run, so the assertions match the visits this test generated and never a
# leftover row from earlier traffic.
VISIT_UID = uuid.uuid4().hex[:12]
VISIT_COOKIE = "sess-" + uuid.uuid4().hex


@pytest.fixture(scope="module")
def visits(admin_db):
    """Generate page visits through the proxy, then drain them with one collector run.

    The proxy logs the request however Grafana or crudman answer it, so the pipeline can
    be tested without authenticating. Noise requests (API, assets, a POST) are sent too and
    must NOT appear, proving the nginx filter holds.
    """
    nav_dashboard = f"/{GRAFANA_PATH}/d/{VISIT_UID}/probe"
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, follow_redirects=False,
                      timeout=10, cookies={"grafana_session": VISIT_COOKIE}) as c:
        c.get(nav_dashboard)                          # grafana dashboard nav -> logged
        c.get(f"/{GRAFANA_PATH}/api/dashboards/uid/{VISIT_UID}")  # API -> skipped
        c.get(f"/{GRAFANA_PATH}/public/build/app.js")            # asset -> skipped
        c.get(f"/{CRUDMAN_PATH}/")                     # crudman page nav -> logged
        c.post(f"/{CRUDMAN_PATH}/login/")              # POST -> skipped

    # nginx buffers the access log, so a pause lets the lines flush before the collector
    # drains visits.log into dashboard_visit.
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
        # crudman views ride the same pipeline.
        with admin_db.cursor() as cur:
            cnt = q(cur,
                f"SELECT count(*) FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE app = 'crudman'")[0]
        assert cnt >= 1, "no crudman page visit was recorded"

    def test_api_and_asset_requests_shall_not_be_recorded(self, admin_db, visits):
        # The noise requests carry the same uid but are API/asset/POST, so the only row
        # with it must be the one dashboard navigation.
        with admin_db.cursor() as cur:
            paths = [r[0] for r in _all(cur,
                f"SELECT url_path FROM {SERVER_STATS_SCHEMA}.dashboard_visit "
                f"WHERE url_path LIKE %s", (f"%{VISIT_UID}%",))]
        assert paths == [f"/{GRAFANA_PATH}/d/{VISIT_UID}/probe"], \
            f"noise requests leaked into visits: {paths}"

    def test_the_session_cookie_shall_be_hashed_not_stored(self, admin_db, visits):
        # The raw cookie must never be stored: session_hash is its md5.
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
