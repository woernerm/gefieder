"""Tenant lifecycle in PostgreSQL: create_tenant / delete_tenant fully provision and
fully tear down a tenant.

A tenant is a login role that owns a ``bronze_<name>`` schema, created and removed by
the database functions in postgresql/initdb/gf_0003_create_functions.sql. These tests run
against the live stack — the crudman admin only forwards to these functions — so they
cover what the per-app unit tests (which mock the database) cannot.

The deletion test reproduces the realistic case that used to leave a tenant
half-deleted: create_tenant grants the role EXECUTE on use_duckdb() and sqlmesh creates
tables in the bronze schema, so a plain DROP ROLE was refused for the lingering
dependencies and — because delete_tenant is atomic — the bronze schema drop was rolled
back with it. delete_tenant now clears the role's grants and owned objects first, so the
whole tenant disappears.
"""
import pytest

# A throwaway tenant name unlikely to collide with anything on the stack.
TENANT = "itest_tenant"
BRONZE = f"bronze_{TENANT}"


def role_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
        return cur.fetchone() is not None


def schema_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,))
        return cur.fetchone() is not None


def listed_tenants(conn):
    """Return the tenant names the crudman admin would list.

    This is the discovery query from crudman/app/tenants/utils.get_tenants(): tenants
    are recognised by their ``bronze_<name>`` schema, and the bare ``<name>`` is recovered
    by stripping the prefix. Keeping a copy here guards the contract that the admin's
    listing query and create_tenant's schema naming stay in agreement — if they drift
    (e.g. one uses a prefix and the other a suffix) a freshly created tenant silently
    fails to appear in the changelist, even though its schema and role exist.
    """
    prefix = "bronze_"
    name_start = len(prefix) + 1  # substr() is 1-based.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT substr(n.nspname, %s) AS name
            FROM pg_catalog.pg_namespace n
            JOIN pg_catalog.pg_roles r ON r.rolname = substr(n.nspname, %s)
            WHERE n.nspname LIKE %s
            ORDER BY name
            """,
            (name_start, name_start, f"{prefix}%"),
        )
        return [row[0] for row in cur.fetchall()]


@pytest.fixture
def clean_tenant(admin_db):
    """Ensure the test tenant does not exist before and after the test."""
    with admin_db.cursor() as cur:
        cur.execute("SELECT delete_tenant(%s)", (TENANT,))
    yield
    with admin_db.cursor() as cur:
        cur.execute("SELECT delete_tenant(%s)", (TENANT,))


class TestTenantLifecycle:
    def test_create_tenant_provisions_schema_and_role(self, admin_db, clean_tenant):
        with admin_db.cursor() as cur:
            cur.execute("SELECT create_tenant(%s, %s)", (TENANT, "supersecret123"))

        assert role_exists(admin_db, TENANT), "tenant role was not created"
        assert schema_exists(admin_db, BRONZE), "tenant bronze schema was not created"

    def test_created_tenant_is_listed_in_the_admin(self, admin_db, clean_tenant):
        # The admin add form succeeds (schema + role exist) but the changelist resyncs
        # from this query; if the query and the schema naming disagree, the new tenant
        # is created in the database yet never appears in the list. Reproduces the
        # "save works but the tenant does not show up" report.
        with admin_db.cursor() as cur:
            cur.execute("SELECT create_tenant(%s, %s)", (TENANT, "supersecret123"))

        assert TENANT in listed_tenants(admin_db), "new tenant is not listed in the admin"

    def test_delete_tenant_removes_schema_and_role(
        self, admin_db, sqlmesh_db, clean_tenant
    ):
        with admin_db.cursor() as cur:
            cur.execute("SELECT create_tenant(%s, %s)", (TENANT, "supersecret123"))

        # Reproduce the dependency that previously blocked DROP ROLE: sqlmesh holds
        # grants on the bronze schema (from create_tenant) and creates a table there.
        with sqlmesh_db.cursor() as cur:
            cur.execute(f"CREATE TABLE {BRONZE}.sqlmesh_table (id int)")

        with admin_db.cursor() as cur:
            cur.execute("SELECT delete_tenant(%s)", (TENANT,))

        assert not schema_exists(admin_db, BRONZE), "bronze schema was not deleted"
        assert not role_exists(admin_db, TENANT), "tenant role was not deleted"
