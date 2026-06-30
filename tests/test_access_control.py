"""Per-user access control: each database role's permissions are spelled out
explicitly, both what it is allowed and what it is forbidden to do.

The expected permission matrix (from postgresql/initdb) is:

  role     | crudman schema            | silver / gold     | sqlmesh schema | bronze*
  ---------+---------------------------+-------------------+----------------+----------
  crudman  | owns (full read/write)    | no access         | no access      | no access
  sqlmesh  | read-only                 | owns (read/write) | owns (full)    | read/write
  grafana  | read model tables only    | read-only         | no access**    | read-only
           | (not auth_/django_ ones)  |                   |                |

  ** grafana sees only the schemas it should chart: bronze_<tenant>, silver and gold (and
     the crudman model tables). It must NOT see sqlmesh's internals -- the state schema
     (sqlmesh), the per-tenant staging schema (silver_staging) or the physical schemas
     behind the virtual layer (sqlmesh__*) -- which hold churning, versioned objects. The
     CREATE SCHEMA event trigger therefore grants grafana read only on bronze_<tenant>
     schemas; silver and gold are granted explicitly in initdb.

  * bronze schemas are created per tenant by create_tenant(); a fresh stack has none, so
    the bronze visibility checks below create a throwaway bronze_<tenant> schema directly.

A representative table is seeded into the relevant schemas by the superuser so the
assertions are deterministic regardless of what the running apps have created.
"""
import psycopg2
import pytest

from conftest import allowed, denied

# Tables the seed fixture creates, addressed per schema.
CRUDMAN_MODEL = "crudman.example_team"        # a non-Django model table
CRUDMAN_DJANGO = "crudman.auth_user"          # a Django-internal table (created by migrations)
SILVER_TABLE = "silver.example_metric"
GOLD_TABLE = "gold.example_metric"


@pytest.fixture(scope="module", autouse=True)
def seed(crudman_db, sqlmesh_db):
    """Seed one representative table per schema, created by the schema's normal writer.

    The tables are created *as the owning role* (not as the superuser with a later
    ownership change), because the cross-role read grants come from ALTER DEFAULT
    PRIVILEGES FOR ROLE <owner>, which only apply to tables that owner creates. This
    mirrors how the real apps populate the schemas, so the access tests are faithful.
    """
    with crudman_db.cursor() as cur:
        # A crudman model table triggers the grant_grafana_read_crudman event trigger,
        # which gives grafana SELECT (and sqlmesh reads it via its default privileges).
        cur.execute("CREATE TABLE IF NOT EXISTS crudman.example_team (id int)")
    with sqlmesh_db.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS silver.example_metric (id int)")
        cur.execute("CREATE TABLE IF NOT EXISTS gold.example_metric (id int)")
    yield
    with crudman_db.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS crudman.example_team")
    with sqlmesh_db.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS silver.example_metric")
        cur.execute("DROP TABLE IF EXISTS gold.example_metric")


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
        allowed(sqlmesh_db, "CREATE TABLE gold.__probe (id int)")
        allowed(sqlmesh_db, "DROP TABLE gold.__probe")

    def test_sqlmesh_shall_read_the_crudman_schema(self, sqlmesh_db):
        allowed(sqlmesh_db, f"SELECT * FROM {CRUDMAN_MODEL}")

    def test_sqlmesh_shall_not_write_the_crudman_schema(self, sqlmesh_db):
        denied(sqlmesh_db, f"INSERT INTO {CRUDMAN_MODEL} VALUES (1)")


class TestGrafanaUser:
    """grafana reads analytics data and crudman model tables, and never writes."""

    @pytest.mark.parametrize("table", [SILVER_TABLE, GOLD_TABLE, CRUDMAN_MODEL])
    def test_grafana_shall_read_analytics_and_crudman_model_tables(self, grafana_db, table):
        allowed(grafana_db, f"SELECT * FROM {table}")

    def test_grafana_shall_not_read_django_internal_tables(self, grafana_db):
        # auth_user holds credentials and must stay hidden.
        denied(grafana_db, f"SELECT * FROM {CRUDMAN_DJANGO}")

    @pytest.mark.parametrize("table", [SILVER_TABLE, GOLD_TABLE, CRUDMAN_MODEL])
    def test_grafana_shall_not_write_anywhere(self, grafana_db, table):
        denied(grafana_db, f"INSERT INTO {table} VALUES (1)")

    def test_grafana_shall_not_read_the_sqlmesh_schema(self, grafana_db):
        # The sqlmesh state schema holds internal bookkeeping; grafana must not see it.
        with grafana_db.cursor() as cur:
            cur.execute("SELECT has_schema_privilege('grafana', 'sqlmesh', 'USAGE')")
            assert cur.fetchone()[0] is False

    def test_grafana_shall_not_read_sqlmesh_internal_schemas(self, admin_db, grafana_db):
        # The physical (sqlmesh__*) and staging (silver_staging) schemas are SQLMesh
        # internals and must stay hidden, even though sqlmesh creates them. They may not
        # exist on a fresh stack (no plan has run), so create representative ones.
        # CREATE SCHEMA IF NOT EXISTS is not atomic, so the live sqlmesh engine creating
        # the same schema concurrently can still raise a duplicate-key error between the
        # check and the create; the test only needs the schema to exist, so ignore it.
        for schema in ("sqlmesh__silver", "silver_staging"):
            with admin_db.cursor() as cur:
                try:
                    cur.execute(
                        f"CREATE SCHEMA IF NOT EXISTS {schema} AUTHORIZATION sqlmesh"
                    )
                except psycopg2.errors.DuplicateSchema:
                    pass  # sqlmesh created it first; that is exactly the state we want
        for schema in ("sqlmesh__silver", "silver_staging"):
            with grafana_db.cursor() as cur:
                cur.execute(
                    "SELECT has_schema_privilege('grafana', %s, 'USAGE')", (schema,)
                )
                assert cur.fetchone()[0] is False, f"grafana can see {schema}"

    def test_grafana_shall_gain_read_access_to_new_bronze_schemas(self, admin_db, grafana_db):
        # The event trigger grants grafana USAGE on a tenant bronze schema as it is created
        # (this is how a newly onboarded tenant's data becomes visible in Grafana).
        with admin_db.cursor() as cur:
            cur.execute(
                "CREATE SCHEMA IF NOT EXISTS bronze_probe AUTHORIZATION sqlmesh"
            )
        try:
            with grafana_db.cursor() as cur:
                cur.execute("SELECT has_schema_privilege('grafana', 'bronze_probe', 'USAGE')")
                assert cur.fetchone()[0] is True
        finally:
            with admin_db.cursor() as cur:
                cur.execute("DROP SCHEMA bronze_probe CASCADE")

    def test_grafana_shall_not_gain_access_to_non_bronze_schemas(self, admin_db, grafana_db):
        # A non-bronze schema created later (e.g. one of sqlmesh's own) must not become
        # visible: the trigger only grants the bronze_<tenant> schemas.
        with admin_db.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS test_probe AUTHORIZATION sqlmesh")
        try:
            with grafana_db.cursor() as cur:
                cur.execute("SELECT has_schema_privilege('grafana', 'test_probe', 'USAGE')")
                assert cur.fetchone()[0] is False
        finally:
            with admin_db.cursor() as cur:
                cur.execute("DROP SCHEMA test_probe CASCADE")
