"""The notebooks: who reaches them, and what a notebook server connects to the database as.

Two boundaries are worth guarding against the running stack. The route has to refuse a
visitor who is not signed in rather than showing them a login form of its own, since the
whole design rests on crudman being the only place accounts exist. And the credential a
server connects with has to be the person's own role, bounded in time -- a password that
never expired would be a standing credential in a process environment, and one issued on
some other role would break the ownership every plan depends on.
"""

import psycopg2
import pytest

from conftest import (
    connect_as,
    REPO,
    CRUDMAN_PATH,
    DB_USER_PREFIX,
    NOTEBOOK_PATH,
    ROLE_PREFIX,
    SQLMESH_DB_USER,
    podman,
)


PERSON = f"{DB_USER_PREFIX}itest_notebook"
PASSWORD = "itest-notebook-password"
EDITOR_ROLE = f"{ROLE_PREFIX}editor"


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
    """The hub has no accounts, so the only way in is crudman's sign-in."""
    response = http_follow.get(f"/{NOTEBOOK_PATH}/")
    assert f"/{CRUDMAN_PATH}/login/" in str(response.url)


def test_whoami_is_not_reachable_through_the_proxy(http):
    """Only the hub, which calls Django directly, gets an answer.

    A page on another site could otherwise rotate somebody's credential with their cookie.
    """
    for request in (http.get, http.post):
        assert request(f"/{CRUDMAN_PATH}/notebooks/whoami/").status_code == 404


def test_the_kernel_loads_sqlmesh():
    """A kernel without the magics looks fine until the first cell fails.

    ipykernel ignores a profile's startup/ files, and an ipython_config.py is read by the
    ipython command only, so this checks exec_files in the kernel's config.
    """
    config = podman(
        "exec", "jupyter", "cat",
        "/etc/skel/.ipython/profile_default/ipython_kernel_config.py",
    )
    assert "exec_files" in config, "the kernel would start without SQLMesh loaded"

    startup = podman("exec", "jupyter", "cat", "/opt/notebook/etc/sqlmesh_startup.py")
    assert "register_magics" in startup
    assert "sqlnotebook.kernel" in startup


def test_the_spawn_environment_carries_the_connection_settings():
    """A missing variable fails silently: the first cell says "Context must be defined"."""
    config = podman("exec", "jupyter", "cat", "/etc/jupyterhub/jupyterhub_config.py")
    kept = config[config.index("env_keep") : config.index("]", config.index("env_keep"))]
    for name in ("PATH", "SQLMESH_HOST", "SQLMESH_PORT", "SQLMESH_DATABASE"):
        assert name in kept, f"{name} would not reach a spawned server"

    quadlet = (REPO / "quadlets" / "jupyter.container").read_text()
    for name in ("SQLMESH_HOST", "SQLMESH_PORT", "SQLMESH_DATABASE"):
        assert f"Environment={name}=" in quadlet, f"{name} is kept but never set"


def test_a_notebook_connects_as_the_person(crudman_db, person):
    """A table a plan creates in a notebook is owned by the person."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            (person, PASSWORD + "-issued", "12 hours"),
        )

    conn = connect_as(person, PASSWORD + "-issued")
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

    conn = connect_as(person, PASSWORD + "-issued")
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT pg_has_role(current_user, '{EDITOR_ROLE}', 'USAGE')")
            assert cur.fetchone()[0]
    finally:
        conn.close()


def test_issuing_replaces_the_previous_password(crudman_db, person):
    """Rotation at every spawn makes a credential in a process environment acceptable."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)", (person, PASSWORD + "-a", "12 hours")
        )
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)", (person, PASSWORD + "-b", "12 hours")
        )

    with pytest.raises(psycopg2.OperationalError):
        connect_as(person, PASSWORD + "-a")


def test_an_expired_password_stops_working(crudman_db, person):
    """A credential left in an environment expires without anyone revoking it."""
    with crudman_db.cursor() as cur:
        cur.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            (person, PASSWORD + "-expired", "-1 second"),
        )

    with pytest.raises(psycopg2.OperationalError):
        connect_as(person, PASSWORD + "-expired")


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
    """Its container reads the podman secret at start; rotating it would break a restart."""
    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "SELECT issue_db_user_password(%s, %s, %s)",
                (SQLMESH_DB_USER, PASSWORD, "12 hours"),
            )


def test_an_unprovisioned_role_gets_no_password(crudman_db, admin_db):
    """Only roles marked by create_db_user, never one made by hand."""
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
    """A token left in an .env must not reactivate an account an administrator closed."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT delete_db_user(%s)", (person,))
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute(
                "SELECT issue_db_user_password(%s, %s, %s)",
                (person, PASSWORD + "-issued", "12 hours"),
            )
