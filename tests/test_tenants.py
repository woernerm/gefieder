"""Tenant lifecycle in PostgreSQL.

create_tenant and delete_tenant fully provision and fully tear down a tenant.

A tenant is a login role owning a ``bronze_<name>`` schema, created and removed by the
functions in gf_0003. These run against the live stack, the crudman admin only forwarding
to those functions, so they cover what the unit tests with their mocked database cannot.

The deletion test reproduces the case that used to leave a tenant half-deleted: the role
holds grants and sqlmesh creates tables in its bronze schema, so a plain DROP ROLE was
refused for the dependencies and, delete_tenant being atomic, the schema drop rolled back
with it. delete_tenant now clears grants and owned objects first.
"""
import pytest
from conftest import BRONZE_SCHEMA_PREFIX

# A throwaway name unlikely to collide with anything on the stack.
TENANT = "itest_tenant"
BRONZE = f"{BRONZE_SCHEMA_PREFIX}{TENANT}"


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

    The discovery query from crudman/app/tenants/utils.get_tenants(): tenants are
    recognised by their ``bronze_<name>`` schema, the bare name recovered by stripping the
    prefix. A copy here guards the contract that the listing query and create_tenant's
    schema naming agree; were they to drift, a new tenant would never appear in the
    changelist although its schema and role exist.
    """
    prefix = BRONZE_SCHEMA_PREFIX
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
        # The add form succeeds while the changelist resyncs from this query, so a
        # disagreement between the two leaves the tenant created but never listed.
        with admin_db.cursor() as cur:
            cur.execute("SELECT create_tenant(%s, %s)", (TENANT, "supersecret123"))

        assert TENANT in listed_tenants(admin_db), "new tenant is not listed in the admin"

    def test_delete_tenant_removes_schema_and_role(
        self, admin_db, sqlmesh_db, clean_tenant
    ):
        with admin_db.cursor() as cur:
            cur.execute("SELECT create_tenant(%s, %s)", (TENANT, "supersecret123"))

        # The dependency that used to block DROP ROLE: sqlmesh holds grants on the
        # bronze schema and creates a table there.
        with sqlmesh_db.cursor() as cur:
            cur.execute(f"CREATE TABLE {BRONZE}.sqlmesh_table (id int)")

        with admin_db.cursor() as cur:
            cur.execute("SELECT delete_tenant(%s)", (TENANT,))

        assert not schema_exists(admin_db, BRONZE), "bronze schema was not deleted"
        assert not role_exists(admin_db, TENANT), "tenant role was not deleted"
