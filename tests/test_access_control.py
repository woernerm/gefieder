"""Per-user access control: what each database role may and may not do.

Every role's permissions are spelled out explicitly, both the allowed and the forbidden.

The expected permission matrix (from postgresql/initdb) is:

  role       | crudman schema            | silver / gold     | sqlmesh schema | bronze*
  -----------+---------------------------+-------------------+----------------+----------
  crudman    | owns (full read/write)    | no access         | no access      | no access
  sqlmesh    | read-only                 | owns (read/write) | owns (full)    | read/write
  dashboards | read model tables only    | read-only         | no access**    | read-only
             | (not auth_/django_ ones)  |                   |                |

  ** dashboards sees only the schemas it should chart: bronze_<project>, silver and gold (and
     the crudman model tables). It must NOT see sqlmesh's internals — the state schema
     (sqlmesh), the per-project staging schema (silver_staging) or the physical schemas
     behind the virtual layer (sqlmesh__*) — which hold churning, versioned objects. The
     CREATE SCHEMA event trigger therefore grants dashboards read only on bronze_<project>
     schemas; silver and gold are granted explicitly in initdb.

  * a bronze schema is created by SQLMesh when a model first names one, so a fresh stack
    has none and the bronze visibility checks below create a throwaway one directly.

DuckDB execution (duckdb.postgres_role, gf_0008) is sqlmesh's and the editor rank's; the
other service roles are refused. The ranks are covered in test_db_users.

A representative table is seeded into each schema, so the assertions hold regardless of
what the running apps have created.
"""
import psycopg2
import pytest

from conftest import (
    BRONZE_SCHEMA_PREFIX,
    GOLD_SCHEMA,
    DASHBOARDS_DB_USER,
    SILVER_SCHEMA,
    SILVER_STAGING_SCHEMA,
    SQLMESH_DB_USER,
    allowed,
    denied,
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
        # A crudman model table fires grant_dashboards_read_crudman, which gives dashboards
        # SELECT; sqlmesh reads it through its default privileges.
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


class TestDashboardsUser:
    """The dashboards role reads analytics data and crudman model tables, and never writes."""

    @pytest.mark.parametrize("table", [SILVER_TABLE, GOLD_TABLE, CRUDMAN_MODEL])
    def test_dashboards_shall_read_analytics_and_crudman_model_tables(self, dashboards_db, table):
        allowed(dashboards_db, f"SELECT * FROM {table}")

    def test_dashboards_shall_not_read_django_internal_tables(self, dashboards_db):
        # auth_user holds credentials.
        denied(dashboards_db, f"SELECT * FROM {CRUDMAN_DJANGO}")

    @pytest.mark.parametrize("table", [SILVER_TABLE, GOLD_TABLE, CRUDMAN_MODEL])
    def test_dashboards_shall_not_write_anywhere(self, dashboards_db, table):
        denied(dashboards_db, f"INSERT INTO {table} VALUES (1)")

    def test_dashboards_shall_not_read_the_sqlmesh_schema(self, dashboards_db):
        # The sqlmesh state schema is internal bookkeeping.
        with dashboards_db.cursor() as cur:
            cur.execute(
                "SELECT has_schema_privilege(%s, 'sqlmesh', 'USAGE')", (DASHBOARDS_DB_USER,)
            )
            assert cur.fetchone()[0] is False

    def test_dashboards_shall_not_read_sqlmesh_internal_schemas(self, admin_db, dashboards_db):
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
            with dashboards_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege(%s, %s, 'USAGE')",
                    (DASHBOARDS_DB_USER, schema),
                )
                assert cur.fetchone()[0] is False, f"dashboards can see {schema}"

    def test_dashboards_shall_gain_read_access_to_new_bronze_schemas(self, admin_db, dashboards_db):
        # The event trigger grants dashboards USAGE on a bronze schema as it is created,
        # which is how a newly added project becomes visible to the dashboards.
        with admin_db.cursor() as cur:
            cur.execute(
                f"CREATE SCHEMA IF NOT EXISTS {BRONZE_PROBE} AUTHORIZATION {SQLMESH_DB_USER}"
            )
        try:
            with dashboards_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege(%s, %s, 'USAGE')",
                    (DASHBOARDS_DB_USER, BRONZE_PROBE),
                )
                assert cur.fetchone()[0] is True
        finally:
            with admin_db.cursor() as cur:
                cur.execute(f"DROP SCHEMA {BRONZE_PROBE} CASCADE")

    def test_dashboards_shall_not_gain_access_to_non_bronze_schemas(self, admin_db, dashboards_db):
        # The trigger grants only the bronze_<project> schemas.
        with admin_db.cursor() as cur:
            cur.execute(
                f"CREATE SCHEMA IF NOT EXISTS test_probe AUTHORIZATION {SQLMESH_DB_USER}"
            )
        try:
            with dashboards_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege(%s, 'test_probe', 'USAGE')",
                    (DASHBOARDS_DB_USER,),
                )
                assert cur.fetchone()[0] is False
        finally:
            with admin_db.cursor() as cur:
                cur.execute("DROP SCHEMA test_probe CASCADE")


class TestDuckDBExecution:
    """pg_duckdb admits the members of <prefix>duckdb and nobody else."""

    def test_sqlmesh_shall_run_a_query_on_duckdb(self, sqlmesh_db):
        with sqlmesh_db.cursor() as cur:
            cur.execute("SELECT use_duckdb(true)")
            try:
                cur.execute(f"EXPLAIN SELECT count(*) FROM {GOLD_TABLE}")
                plan = "\n".join(row[0] for row in cur.fetchall())
            finally:
                # Session-wide, on a connection the other tests share.
                cur.execute("SELECT use_duckdb(false)")
        assert "DuckDBScan" in plan, plan

    @pytest.mark.parametrize("role", ["crudman_db", "dashboards_db"])
    def test_the_other_service_roles_shall_be_refused(self, request, role):
        conn = request.getfixturevalue(role)
        with conn.cursor() as cur:
            # Not InsufficientPrivilege: pg_duckdb raises it as an internal error.
            with pytest.raises(psycopg2.errors.InternalError, match="duckdb.postgres_role"):
                cur.execute("SELECT * FROM duckdb.query('SELECT 1')")
