"""The notebooks: who reaches them, and what a notebook server connects to the database as.

Two boundaries are worth guarding against the running stack. The route has to refuse a
visitor who is not signed in rather than showing them a login form of its own, since the
whole design rests on crudman being the only place accounts exist. And the credential a
server connects with has to be the person's own role, bounded in time -- a password that
never expired would be a standing credential in a process environment, and one issued on
some other role would break the ownership every plan depends on.
"""
from pathlib import Path

import psycopg2
import pytest

from conftest import (
    CRUDMAN_PATH,
    DB_USER_PREFIX,
    NOTEBOOK_PATH,
    PG_DATABASE,
    PG_PORT,
    ROLE_PREFIX,
    SQLMESH_DB_USER,
    podman,
)

REPO = Path(__file__).resolve().parents[1]

PERSON = f"{DB_USER_PREFIX}itest_notebook"
PASSWORD = "itest-notebook-password"
EDITOR_ROLE = f"{ROLE_PREFIX}editor"


def connect(user, password):
    """A connection as one database role, for asserting a credential works or does not."""
    return psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=PG_DATABASE,
        user=user, password=password,
    )


@pytest.fixture
def person(crudman_db, admin_db):
    """A provisioned person, removed either side of the test."""

    def drop():
        with admin_db.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (PERSON,))
            if cur.fetchone():
                cur.execute(f'DROP OWNED BY "{PERSON}" CASCADE')
                cur.execute(f'DROP ROLE "{PERSON}"')

    drop()
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_db_user(%s, %s, %s)", (PERSON, PASSWORD, EDITOR_ROLE))
    yield PERSON
    drop()


def test_the_route_is_served(http):
    """The proxy forwards the path, and the hub answers on it rather than 404."""
    response = http.get(f"/{NOTEBOOK_PATH}/")
    assert response.status_code < 500


def test_an_anonymous_visitor_is_sent_to_the_admin_panel_to_sign_in(http_follow):
    """No second login screen: the hub has no accounts, so the only way in is the sign-in
    crudman already offers, which with single sign-on on goes to the provider."""
    response = http_follow.get(f"/{NOTEBOOK_PATH}/")
    assert f"/{CRUDMAN_PATH}/login/" in str(response.url)


def test_whoami_is_not_reachable_through_the_proxy(http):
    """The path is under the admin panel's, so a browser could otherwise reach it -- and a
    page on another site could make one rotate somebody's credential with their cookie. It
    answers the hub, which calls Django directly, and nobody who arrives through nginx."""
    for request in (http.get, http.post):
        assert request(f"/{CRUDMAN_PATH}/notebooks/whoami/").status_code == 404


def test_the_kernel_loads_sqlmesh():
    """A kernel that starts without the magics looks fine until the first cell fails, so
    the configuration that loads them is checked rather than assumed.

    ipykernel does not run a profile's startup/ files -- one dropped there is silently
    never executed -- which is why this goes through exec_files instead, in the file a
    *kernel* reads: an ipython_config.py beside it is read by the ipython command only."""
    config = podman(
        "exec", "jupyter", "cat",
        "/etc/skel/.ipython/profile_default/ipython_kernel_config.py",
    )
    assert "exec_files" in config, "the kernel would start without SQLMesh loaded"

    startup = podman("exec", "jupyter", "cat", "/opt/notebook/etc/sqlmesh_startup.py")
    assert "register_magics" in startup
    assert "sqlnotebook.kernel" in startup


def test_the_spawn_environment_carries_the_connection_settings():
    """A spawned server inherits almost nothing from this container, so what the SQLMesh
    config needs has to be listed. Missing one is silent: the kernel starts, and the first
    cell fails with "Context must be defined"."""
    config = podman("exec", "jupyter", "cat", "/etc/jupyterhub/jupyterhub_config.py")
    kept = config[config.index("env_keep") : config.index("]", config.index("env_keep"))]
    for name in ("PATH", "SQLMESH_HOST", "SQLMESH_PORT", "SQLMESH_DATABASE"):
        assert name in kept, f"{name} would not reach a spawned server"

    quadlet = (REPO / "quadlets" / "jupyter.container").read_text()
    for name in ("SQLMESH_HOST", "SQLMESH_PORT", "SQLMESH_DATABASE"):
        assert f"Environment={name}=" in quadlet, f"{name} is kept but never set"


def test_a_notebook_connects_as_the_person(crudman_db, person):
    """The point of the whole arrangement: a table a plan creates in a notebook is owned
    by the person, exactly as if they had connected from a laptop. There is no second
    account to assume, so current_user is simply them."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            (person, PASSWORD + "-issued", "12 hours"),
        )

    conn = connect(person, PASSWORD + "-issued")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, session_user")
            current, session = cur.fetchone()
    finally:
        conn.close()

    assert current == person
    assert session == person


def test_the_rank_reaches_a_notebook_session(crudman_db, person):
    """The rank hangs off the person's role, so an editor may write where an editor may."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            (person, PASSWORD + "-issued", "12 hours"),
        )

    conn = connect(person, PASSWORD + "-issued")
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT pg_has_role(current_user, '{EDITOR_ROLE}', 'USAGE')")
            assert cur.fetchone()[0]
    finally:
        conn.close()


def test_issuing_replaces_the_previous_password(crudman_db, person):
    """Rotated at every spawn, which is what makes a credential in a process environment
    acceptable: the previous one stops working."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)", (person, PASSWORD + "-a", "12 hours")
        )
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)", (person, PASSWORD + "-b", "12 hours")
        )

    with pytest.raises(psycopg2.OperationalError):
        connect(person, PASSWORD + "-a")


def test_an_expired_password_stops_working(crudman_db, person):
    """The whole of the expiry: past the deadline PostgreSQL refuses the password, so a
    credential left in an environment stops being useful without anyone revoking it."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            (person, PASSWORD + "-expired", "-1 second"),
        )

    with pytest.raises(psycopg2.OperationalError):
        connect(person, PASSWORD + "-expired")


def test_the_expiry_is_reported(crudman_db, person):
    """The caller caches the password until it expires, so it has to be told when."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            (person, PASSWORD + "-issued", "12 hours"),
        )
        expires_at = cur.fetchone()[0]
        cur.execute("SELECT now()")
        assert expires_at > cur.fetchone()[0]


def test_a_service_role_gets_no_password(crudman_db):
    """A service role's password is a podman secret its container reads at start, so
    rotating one here would take that component down at its next restart."""
    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "SELECT issue_db_user_password(%s, %s, %s)",
                (SQLMESH_DB_USER, PASSWORD, "12 hours"),
            )


def test_an_unprovisioned_role_gets_no_password(crudman_db, admin_db):
    """Only a role carrying the marker create_db_user grants, so this cannot mint a
    credential for a role somebody made by hand."""
    with admin_db.cursor() as cur:
        cur.execute("DROP ROLE IF EXISTS itest_outsider")
        cur.execute("CREATE ROLE itest_outsider LOGIN")

    try:
        with crudman_db.cursor() as cur:
            with pytest.raises(psycopg2.errors.RaiseException):
                cur.execute(
                    "SELECT issue_db_user_password(%s, %s, %s)",
                    ("itest_outsider", PASSWORD, "12 hours"),
                )
    finally:
        with admin_db.cursor() as cur:
            cur.execute("DROP ROLE IF EXISTS itest_outsider")


def test_a_disabled_person_gets_no_password(crudman_db, person):
    """Offboarding takes LOGIN away; issuing must not hand it back, or a token still in
    somebody's .env would reactivate an account an administrator closed."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT delete_db_user(%s)", (person,))
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "SELECT issue_db_user_password(%s, %s, %s)",
                (person, PASSWORD + "-issued", "12 hours"),
            )
