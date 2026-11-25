"""What each database role may and may not do, per postgresql/initdb.

  role     | crudman schema            | silver / gold     | sqlmesh schema | bronze*
  ---------+---------------------------+-------------------+----------------+----------
  crudman  | owns (full read/write)    | no access         | no access      | no access
  sqlmesh  | read-only                 | owns (read/write) | owns (full)    | read/write
  grafana  | read model tables only    | read-only         | no access**    | read-only
           | (not auth_/django_ ones)  |                   |                |

  ** Nor silver_staging or the physical schemas behind the virtual layer (sqlmesh__*),
     which hold churning, versioned objects.
  * SQLMesh creates a bronze schema when a model first names one, so the checks below
    create a throwaway one.

A representative table is seeded into each schema, independent of what the apps created.
"""
import subprocess

import psycopg2
import pytest

from conftest import (
    BRONZE_SCHEMA_PREFIX,
    DB_PASSWORDS,
    GOLD_SCHEMA,
    GRAFANA_DB_USER,
    PG_DATABASE,
    SILVER_SCHEMA,
    SILVER_STAGING_SCHEMA,
    SQLMESH_DB_USER,
    allowed,
    denied,
    podman,
)

# Tables the seed fixture creates, addressed per schema.
CRUDMAN_MODEL = "crudman.example_team"        # a non-Django model table
CRUDMAN_DJANGO = "crudman.auth_user"          # a Django-internal table (created by migrations)
# A throwaway schema that looks like a project's, to watch the event trigger fire.
BRONZE_PROBE = f"{BRONZE_SCHEMA_PREFIX}probe"
SILVER_TABLE = f"{SILVER_SCHEMA}.example_metric"
GOLD_TABLE = f"{GOLD_SCHEMA}.example_metric"


@pytest.fixture(scope="module", autouse=True)
def seed(crudman_db, sqlmesh_db):
    """Seed one representative table per schema, created by the schema's normal writer.

    Created *as the owning role* rather than as the superuser, because the cross-role
    read grants come from ALTER DEFAULT PRIVILEGES FOR ROLE <owner> and apply only to
    tables that owner creates.
    """
    with crudman_db.cursor() as cur:
        # A crudman model table fires grant_crudman_read, which gives both roles SELECT.
        cur.execute("CREATE TABLE IF NOT EXISTS crudman.example_team (id int)")
    with sqlmesh_db.cursor() as cur:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {SILVER_TABLE} (id int)")
        cur.execute(f"CREATE TABLE IF NOT EXISTS {GOLD_TABLE} (id int)")
    yield
    with crudman_db.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS crudman.example_team")
    with sqlmesh_db.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {SILVER_TABLE}")
        cur.execute(f"DROP TABLE IF EXISTS {GOLD_TABLE}")


class TestLoopback:
    """The whole pod shares the loopback, so being on it must not stand in for a password."""

    @pytest.mark.parametrize("user", DB_PASSWORDS)
    def test_no_role_shall_connect_without_a_password(self, user):
        with pytest.raises(subprocess.CalledProcessError) as refusal:
            podman("exec", "postgresql", "psql", "--no-password", "--host", "127.0.0.1",
                   "--username", user, "--dbname", PG_DATABASE, "--command", "SELECT 1")
        assert "no password supplied" in refusal.value.stderr

    def test_no_network_rule_shall_trust(self, admin_db):
        with admin_db.cursor() as cur:
            cur.execute("SELECT line_number FROM pg_hba_file_rules "
                        "WHERE type <> 'local' AND auth_method = 'trust'")
            assert cur.fetchall() == []


class TestCrudmanUser:
    """crudman owns its own schema and has no access to the analytics schemas."""

    def test_crudman_shall_read_and_write_its_own_schema(self, crudman_db):
        allowed(crudman_db, f"SELECT * FROM {CRUDMAN_MODEL}")
        allowed(crudman_db, f"INSERT INTO {CRUDMAN_MODEL} VALUES (1)")

    def test_crudman_shall_create_tables_in_its_own_schema(self, crudman_db):
        allowed(crudman_db, "CREATE TABLE crudman.__probe (id int)")
        allowed(crudman_db, "DROP TABLE crudman.__probe")

    def test_crudman_shall_not_read_the_analytics_schemas(self, crudman_db):
        denied(crudman_db, f"SELECT * FROM {SILVER_TABLE}")
        denied(crudman_db, f"SELECT * FROM {GOLD_TABLE}")

    def test_crudman_shall_not_write_the_analytics_schemas(self, crudman_db):
        denied(crudman_db, f"INSERT INTO {GOLD_TABLE} VALUES (1)")


class TestSqlmeshUser:
    """sqlmesh owns the analytics schemas and reads, but does not write, crudman."""

    def test_sqlmesh_shall_read_and_write_the_analytics_schemas(self, sqlmesh_db):
        allowed(sqlmesh_db, f"SELECT * FROM {GOLD_TABLE}")
        allowed(sqlmesh_db, f"INSERT INTO {GOLD_TABLE} VALUES (1)")

    def test_sqlmesh_shall_create_tables_in_the_analytics_schemas(self, sqlmesh_db):
        allowed(sqlmesh_db, f"CREATE TABLE {GOLD_SCHEMA}.__probe (id int)")
        allowed(sqlmesh_db, f"DROP TABLE {GOLD_SCHEMA}.__probe")

    def test_sqlmesh_shall_read_the_crudman_schema(self, sqlmesh_db):
        allowed(sqlmesh_db, f"SELECT * FROM {CRUDMAN_MODEL}")

    def test_sqlmesh_shall_not_write_the_crudman_schema(self, sqlmesh_db):
        denied(sqlmesh_db, f"INSERT INTO {CRUDMAN_MODEL} VALUES (1)")


class TestPrivateTables:
    """Whoever may ship a model or open a dashboard reads nothing that signs somebody in."""

    @pytest.mark.parametrize("table", ["auth_user", "django_session", "dbusers_databaseuser",
                                       "dropzones_dropzone"])
    def test_they_shall_be_closed_to_the_analytics_roles(self, sqlmesh_db, grafana_db, table):
        denied(sqlmesh_db, f"SELECT * FROM crudman.{table}")
        denied(grafana_db, f"SELECT * FROM crudman.{table}")

    def test_the_uploads_shall_stay_readable(self, sqlmesh_db, grafana_db):
        for table in ("dropzones_upload", "dropzones_uploadfile"):
            allowed(sqlmesh_db, f"SELECT * FROM crudman.{table}")
            allowed(grafana_db, f"SELECT * FROM crudman.{table}")

    def test_the_query_statistics_shall_hold_no_password(self, admin_db):
        with admin_db.cursor() as cur:
            cur.execute("CREATE ROLE itest_statistics LOGIN PASSWORD 'itest-in-plain-text'")
            cur.execute("DROP ROLE itest_statistics")
            cur.execute("SELECT count(*) FROM pg_stat_statements "
                        "WHERE query LIKE '%%itest-in-plain' || '-text%%' OR query ~* %s",
                        (r"^\s*(create|alter)\s+role\M.*\mpassword\M",))
            assert cur.fetchone()[0] == 0


class TestGrafanaUser:
    """grafana reads analytics data and crudman model tables, and never writes."""

    @pytest.mark.parametrize("table", [SILVER_TABLE, GOLD_TABLE, CRUDMAN_MODEL])
    def test_grafana_shall_read_analytics_and_crudman_model_tables(self, grafana_db, table):
        allowed(grafana_db, f"SELECT * FROM {table}")

    def test_grafana_shall_not_read_django_internal_tables(self, grafana_db):
        # auth_user holds credentials.
        denied(grafana_db, f"SELECT * FROM {CRUDMAN_DJANGO}")

    @pytest.mark.parametrize("table", [SILVER_TABLE, GOLD_TABLE, CRUDMAN_MODEL])
    def test_grafana_shall_not_write_anywhere(self, grafana_db, table):
        denied(grafana_db, f"INSERT INTO {table} VALUES (1)")

    def test_grafana_shall_not_read_the_sqlmesh_schema(self, grafana_db):
        # The sqlmesh state schema is internal bookkeeping.
        with grafana_db.cursor() as cur:
            cur.execute(
                "SELECT has_schema_privilege(%s, 'sqlmesh', 'USAGE')", (GRAFANA_DB_USER,)
            )
            assert cur.fetchone()[0] is False

    def test_grafana_shall_not_read_sqlmesh_internal_schemas(self, admin_db, grafana_db):
        # The physical (sqlmesh__*) and staging (silver_staging) schemas are SQLMesh
        # internals and must stay hidden. They may not exist on a fresh stack, so create
        # representative ones. CREATE SCHEMA IF NOT EXISTS is not atomic, so the live
        # engine creating the same schema can still raise a duplicate key; ignore it.
        for schema in (f"sqlmesh__{SILVER_SCHEMA}", SILVER_STAGING_SCHEMA):
            with admin_db.cursor() as cur:
                try:
                    cur.execute(
                        f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION {SQLMESH_DB_USER}"
                    )
                except psycopg2.errors.DuplicateSchema:
                    pass  # sqlmesh created it first; that is exactly the state we want
        for schema in (f"sqlmesh__{SILVER_SCHEMA}", SILVER_STAGING_SCHEMA):
            with grafana_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege(%s, %s, 'USAGE')",
                    (GRAFANA_DB_USER, schema),
                )
                assert cur.fetchone()[0] is False, f"grafana can see {schema}"

    def test_grafana_shall_gain_read_access_to_new_bronze_schemas(self, admin_db, grafana_db):
        # The event trigger grants grafana USAGE on a bronze schema as it is created,
        # which is how a newly added project becomes visible in Grafana.
        with admin_db.cursor() as cur:
            cur.execute(
                f"CREATE SCHEMA IF NOT EXISTS {BRONZE_PROBE} AUTHORIZATION {SQLMESH_DB_USER}"
            )
        try:
            with grafana_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege(%s, %s, 'USAGE')",
                    (GRAFANA_DB_USER, BRONZE_PROBE),
                )
                assert cur.fetchone()[0] is True
        finally:
            with admin_db.cursor() as cur:
                cur.execute(f"DROP SCHEMA {BRONZE_PROBE} CASCADE")

    def test_grafana_shall_not_gain_access_to_non_bronze_schemas(self, admin_db, grafana_db):
        # The trigger grants only the bronze_<project> schemas.
        with admin_db.cursor() as cur:
            cur.execute(
                f"CREATE SCHEMA IF NOT EXISTS test_probe AUTHORIZATION {SQLMESH_DB_USER}"
            )
        try:
            with grafana_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege(%s, 'test_probe', 'USAGE')",
                    (GRAFANA_DB_USER,),
                )
                assert cur.fetchone()[0] is False
        finally:
            with admin_db.cursor() as cur:
                cur.execute("DROP SCHEMA test_probe CASCADE")
