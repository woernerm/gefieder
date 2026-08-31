"""Per-administrator database accounts.

create_db_user and delete_db_user provision and lock a personal login role with the
privileges its rank carries.

An administrator who develops SQLMesh models gets a role of their own instead of sharing
the sqlmesh secret. The role is created by the SECURITY DEFINER functions in
postgresql/initdb/gf_0003_create_functions.sql and takes its privileges from the
group roles in gf_0008_create_admin_roles.sql; crudman only forwards to them, so these
tests run against the live stack.

What is worth guarding here is the boundary the functions draw. They run as their
superuser owner, so an unprivileged caller reaching them could otherwise create a role of
any rank — the group_role argument is checked against a fixed list for exactly that
reason, and the test below is what keeps the check honest."""
import psycopg2
import pytest

from conftest import (
    CRUDMAN_DB_USER,
    DB_USER_PREFIX,
    ROLE_PREFIX,
    GRAFANA_DB_USER,
    PG_DATABASE,
    SILVER_SCHEMA,
    SQLMESH_DB_USER,
    SUPERUSER_NAME,
)

# The three ranks gf_0008 created, spelled from the configured prefix rather than listed:
# a hardcoded name would pass against a deployment that has no such role.
VIEWER_ROLE = f"{ROLE_PREFIX}viewer"
EDITOR_ROLE = f"{ROLE_PREFIX}editor"
ADMIN_ROLE = f"{ROLE_PREFIX}admin"

# The marker every provisioned account holds, which is what the functions check before
# they modify or drop one -- the name is only readability, DB_USER_PREFIX being optional.
MARKER_ROLE = f"{ROLE_PREFIX}person"

# Throwaway account names, carrying the personal-account prefix crudman derives
# (dbusers.utils).
VIEWER = f"{DB_USER_PREFIX}itest_viewer"
EDITOR = f"{DB_USER_PREFIX}itest_editor"
PASSWORD = "itest-password-long-enough"

# A throwaway tenant, to stand in for the roles that share the namespace without being
# personal accounts. Its own lifecycle is tests/test_tenants.py.
TENANT = "itest_dbuser_tenant"
TENANT_PASSWORD = "itest-tenant-password"


def role_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
        return cur.fetchone() is not None


def can_login(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (name,))
        row = cur.fetchone()
        return row is not None and row[0]


def memberships(conn, name):
    """The group roles `name` is a direct member of."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.rolname
            FROM pg_auth_members m
            JOIN pg_roles g ON g.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname = %s
            """,
            (name,),
        )
        return {row[0] for row in cur.fetchall()}


@pytest.fixture
def cleanup(admin_db):
    """Remove the throwaway roles before and after, so a failed run does not poison the next."""

    def drop():
        with admin_db.cursor() as cur:
            for name in (VIEWER, EDITOR, TENANT):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
                if cur.fetchone():
                    # DROP OWNED BY clears the grants the functions handed out; without it
                    # the drop is refused for the dependencies, as with tenants.
                    cur.execute(f'DROP OWNED BY "{name}" CASCADE')
                    cur.execute(f'DROP ROLE "{name}"')

    drop()
    yield
    drop()


def test_group_roles_exist(admin_db):
    """The three ranks and the marker are created at first start, none of them a login."""
    for name in (VIEWER_ROLE, EDITOR_ROLE, ADMIN_ROLE, MARKER_ROLE):
        assert role_exists(admin_db, name), f"{name} is missing"
        assert not can_login(admin_db, name), f"{name} must not be a login role"


def test_create_db_user_provisions_role(crudman_db, admin_db, cleanup):
    """crudman can provision an account, and it lands in the rank it was given."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))

    assert role_exists(admin_db, VIEWER)
    assert can_login(admin_db, VIEWER)
    assert VIEWER_ROLE in memberships(admin_db, VIEWER)
    assert MARKER_ROLE in memberships(admin_db, VIEWER), "the account must be marked as ours"


@pytest.fixture
def tenant(crudman_db, cleanup):
    """A tenant role, created the way the admin creates one."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_tenant(%s, %s)", (TENANT, TENANT_PASSWORD))
    yield TENANT
    with crudman_db.cursor() as cur:
        cur.execute("SELECT delete_tenant(%s)", (TENANT,))


def test_a_tenant_role_shall_not_be_taken_over(crudman_db, admin_db, tenant):
    """create_db_user refuses a name already held by something that is not an account.

    The name alone cannot say: tenants are the bare tenant name and share the namespace,
    so without this a mistyped provisioning would reset a tenant's password and hand the
    person its bronze schema.
    """
    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "SELECT create_db_user(%s, %s, %s)", (tenant, PASSWORD, VIEWER_ROLE)
            )

    assert MARKER_ROLE not in memberships(admin_db, tenant)


def test_a_tenant_role_shall_not_be_dropped_or_disabled(crudman_db, tenant):
    """The destructive functions refuse it too, for the same reason.

    drop_db_user takes the bronze schema with the role, so this is the one that would
    destroy data rather than merely confuse the admin.
    """
    for function in ("drop_db_user", "delete_db_user", "clear_db_user_password"):
        with crudman_db.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute(f"SELECT {function}(%s)", (tenant,))


def test_a_provisioned_account_shall_not_become_a_tenant(crudman_db, cleanup):
    """And the reverse: create_tenant refuses a name a person already holds."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))

    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("SELECT create_tenant(%s, %s)", (VIEWER, TENANT_PASSWORD))


def test_a_disabled_account_shall_stay_droppable(crudman_db, admin_db, cleanup):
    """The marker survives offboarding, which strips the rank memberships.

    Were the rank the marker, an account disabled by delete_db_user could never be
    removed afterwards -- and disable-then-remove is the ordinary way someone leaves.
    """
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))
        cur.execute("SELECT delete_db_user(%s)", (VIEWER,))
        cur.execute("SELECT drop_db_user(%s)", (VIEWER,))

    assert not role_exists(admin_db, VIEWER)


def test_rank_change_replaces_membership(crudman_db, admin_db, cleanup):
    """A promotion removes the old rank rather than adding a second one.

    Both ranks at once would leave a demotion silently ineffective, since the more
    privileged membership would survive it.
    """
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (EDITOR, PASSWORD, VIEWER_ROLE))
        # A NULL password is how crudman re-ranks someone without issuing a new
        # credential; the person keeps the password they already have.
        cur.execute("SELECT create_db_user(%s, %s, %s)", (EDITOR, None, EDITOR_ROLE))

    # The marker is not a rank and is not touched by re-ranking; see is_db_user.
    assert memberships(admin_db, EDITOR) == {EDITOR_ROLE, MARKER_ROLE}
    assert can_login(admin_db, EDITOR), "re-ranking must not lock the account out"


def test_unknown_group_role_is_refused(crudman_db, cleanup):
    """The rank argument is checked against a fixed list.

    Without this the SECURITY DEFINER function would be a way for the application role to
    grant itself membership of any role in the cluster, superusers included.
    """
    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, "postgres")
            )


def test_delete_db_user_disables_without_dropping(crudman_db, admin_db, cleanup):
    """Offboarding locks the account but keeps the role, so owned objects survive."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))
        cur.execute("SELECT delete_db_user(%s)", (VIEWER,))

    assert role_exists(admin_db, VIEWER), "the role must survive so its objects keep an owner"
    assert not can_login(admin_db, VIEWER)
    # Everything but the marker, which has to outlive offboarding for the role to stay
    # droppable afterwards.
    assert memberships(admin_db, VIEWER) == {MARKER_ROLE}, "privileges are revoked on offboarding"


# The roles the provisioning functions must refuse. The superuser is named by
# SUPERUSER_NAME (buildtime.env) rather than being "postgres", which is exactly the case a
# hardcoded list would miss.
SERVICE_ROLES = (SUPERUSER_NAME, CRUDMAN_DB_USER, SQLMESH_DB_USER, GRAFANA_DB_USER)


def test_service_roles_are_protected(crudman_db):
    """delete_db_user refuses the service roles, whose names are valid identifiers too."""
    for name in SERVICE_ROLES:
        with crudman_db.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("SELECT delete_db_user(%s)", (name,))


def test_the_configured_superuser_is_recognised(admin_db):
    """is_protected_role derives the superuser rather than assuming it is called "postgres".

    SUPERUSER_NAME is configurable, so a listed name would protect a role that does not
    exist on this deployment and leave the real superuser reachable.
    """
    with admin_db.cursor() as cur:
        cur.execute("SELECT is_protected_role(%s)", (SUPERUSER_NAME,))
        assert cur.fetchone()[0], f"{SUPERUSER_NAME} must be recognised as protected"

        cur.execute("SELECT is_protected_role(%s)", (VIEWER,))
        assert not cur.fetchone()[0], "a provisioned account must not be protected"


def test_provisioning_functions_are_not_public(connect, cleanup):
    """Only crudman may provision.

    The functions run as their superuser owner, so a default PUBLIC grant would let
    any role — a tenant, grafana — mint an admin account.
    """
    grafana = connect(GRAFANA_DB_USER)
    with grafana.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, ADMIN_ROLE))


def test_editor_can_read_analytics(crudman_db, admin_db, cleanup):
    """A provisioned editor reaches the medallion layers the dashboards read."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (EDITOR, PASSWORD, EDITOR_ROLE))

    conn = psycopg2.connect(
        host="localhost",
        port=admin_db.get_dsn_parameters()["port"],
        dbname=PG_DATABASE,
        user=EDITOR,
        password=PASSWORD,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT has_schema_privilege(%s, 'USAGE')", (SILVER_SCHEMA,))
            assert cur.fetchone()[0], "an editor must be able to read the silver layer"
    finally:
        conn.close()


def has_password(conn, name):
    """Whether the role carries a password at all.

    With scram-sha-256 a role holding none cannot authenticate, which is what "enrolled but
    not yet claimed" looks like in the database.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rolpassword IS NOT NULL FROM pg_authid WHERE rolname = %s", (name,)
        )
        row = cur.fetchone()
        return row is not None and row[0]


def test_enrolled_account_has_no_password_until_claimed(crudman_db, admin_db, cleanup):
    """An administrator enrolls someone without ever handling a credential.

    The role exists so its rank is settled, but carries no password, so it cannot connect
    until the person's next sign-in issues one to them.
    """
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, None, VIEWER_ROLE))

    assert role_exists(admin_db, VIEWER)
    assert not has_password(admin_db, VIEWER), "an unclaimed account must hold no password"


def test_issuing_the_password_makes_the_account_usable(crudman_db, admin_db, cleanup):
    """The second half of provisioning, as the login signal performs it."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, None, VIEWER_ROLE))
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))

    assert has_password(admin_db, VIEWER)

    conn = psycopg2.connect(
        host="localhost",
        port=admin_db.get_dsn_parameters()["port"],
        dbname=PG_DATABASE,
        user=VIEWER,
        password=PASSWORD,
    )
    conn.close()


def test_clearing_a_password_locks_the_account_immediately(crudman_db, admin_db, cleanup):
    """A reset takes effect at once, not at the person's next sign-in.

    That is the point of clearing it here rather than waiting: a credential that may have
    leaked stops working now.
    """
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))
        cur.execute("SELECT clear_db_user_password(%s)", (VIEWER,))

    assert not has_password(admin_db, VIEWER)

    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(
            host="localhost",
            port=admin_db.get_dsn_parameters()["port"],
            dbname=PG_DATABASE,
            user=VIEWER,
            password=PASSWORD,
        )


def test_drop_db_user_removes_the_role(crudman_db, admin_db, cleanup):
    """The destructive counterpart to delete_db_user: the role itself is gone."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (VIEWER, PASSWORD, VIEWER_ROLE))
        cur.execute("SELECT drop_db_user(%s)", (VIEWER,))

    assert not role_exists(admin_db, VIEWER)


def test_drop_db_user_refuses_the_service_roles(crudman_db):
    for name in SERVICE_ROLES:
        with crudman_db.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute("SELECT drop_db_user(%s)", (name,))


def test_drop_db_user_refuses_a_tenant_role(crudman_db, admin_db):
    """Only provisioned personal accounts may be dropped.

    Tenant roles own a bronze schema, so dropping one by passing the wrong name would take
    a tenant's data with it. The personal-account prefix is what separates the two.
    """
    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("SELECT drop_db_user(%s)", ("project_a",))

    assert role_exists(admin_db, "project_a"), "the tenant role must be untouched"


def test_dropping_is_not_reachable_by_other_roles(connect, cleanup):
    """As with the other provisioning functions: crudman only."""
    grafana = connect(GRAFANA_DB_USER)
    with grafana.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute("SELECT drop_db_user(%s)", (VIEWER,))
