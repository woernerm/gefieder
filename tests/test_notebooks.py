"""The notebooks: who reaches them, and what a notebook server connects to the database as.

Two boundaries are worth guarding against the running stack. The route has to refuse a
visitor who is not signed in rather than showing them a login form of its own, since the
whole design rests on crudman being the only place accounts exist. And the sibling login a
server connects with has to *be* the person -- a session that reported some other
current_user would break the ownership every plan depends on, and one that outlived the
person's account would be a credential nobody is watching.
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
NOTEBOOK = f"{PERSON}_nb"
PASSWORD = "itest-notebook-password"
EDITOR_ROLE = f"{ROLE_PREFIX}editor"
MARKER_ROLE = f"{ROLE_PREFIX}person"


def role_exists(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
        return cur.fetchone() is not None


@pytest.fixture
def person(crudman_db, admin_db):
    """A provisioned person with a notebook login, removed either side of the test."""

    def drop():
        with admin_db.cursor() as cur:
            for name in (NOTEBOOK, PERSON):
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
                if cur.fetchone():
                    cur.execute(f'DROP OWNED BY "{name}" CASCADE')
                    cur.execute(f'DROP ROLE "{name}"')

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


def test_create_notebook_login_provisions_a_sibling(crudman_db, admin_db, person):
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD))
        assert cur.fetchone()[0] == NOTEBOOK

    assert role_exists(admin_db, NOTEBOOK)
    with admin_db.cursor() as cur:
        cur.execute(
            """
            SELECT g.rolname FROM pg_auth_members m
            JOIN pg_roles g ON g.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname = %s
            """,
            (NOTEBOOK,),
        )
        memberships = {row[0] for row in cur.fetchall()}
    assert person in memberships, "the notebook login carries the person's rights"
    assert MARKER_ROLE in memberships, "it must be recognisable as one of ours"


def test_a_notebook_session_can_become_the_person(crudman_db, person):
    """The point of the whole arrangement: a table a plan creates in a notebook is owned
    by the person, exactly as if they had connected from a laptop.

    The session assumes the role, which is what SQLMesh's "role" connection setting does
    on every cursor (sqlmesh/config.py). Membership is what permits it, and that is what
    create_notebook_login grants."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD))

    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=PG_DATABASE,
        user=NOTEBOOK, password=PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f'SET ROLE "{person}"')
            cur.execute("SELECT current_user, session_user")
            current, session = cur.fetchone()
    finally:
        conn.close()

    assert current == person, "current_user must be the person, not the notebook login"
    assert session == NOTEBOOK, "and the connection is still traceable as a notebook"


def test_the_rank_reaches_a_notebook_session(crudman_db, person):
    """The rank hangs off the person's role, so an editor may write where an editor may."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD))

    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=PG_DATABASE,
        user=NOTEBOOK, password=PASSWORD,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f'SET ROLE "{person}"')
            cur.execute(f"SELECT pg_has_role(current_user, '{EDITOR_ROLE}', 'USAGE')")
            assert cur.fetchone()[0]
    finally:
        conn.close()


def test_rotating_replaces_the_password(crudman_db, person):
    """Rotated at every spawn, which is what makes a credential in a process environment
    acceptable: the previous one stops working."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD))
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD + "-new"))

    with pytest.raises(psycopg2.OperationalError):
        psycopg2.connect(
            host="localhost", port=PG_PORT, dbname=PG_DATABASE,
            user=NOTEBOOK, password=PASSWORD,
        )


def test_disabling_the_person_removes_the_notebook_login(crudman_db, admin_db, person):
    """Offboarding must not leave a working credential behind. The person's own role is
    disabled rather than dropped, so nothing here can be inferred from its absence."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD))
        cur.execute("SELECT delete_db_user(%s)", (person,))

    assert not role_exists(admin_db, NOTEBOOK)
    assert role_exists(admin_db, person), "the person keeps what they own"


def test_dropping_the_person_removes_the_notebook_login(crudman_db, admin_db, person):
    """And the destructive path too, where the sibling's membership would otherwise make
    PostgreSQL refuse the drop."""
    with crudman_db.cursor() as cur:
        cur.execute("SELECT create_notebook_login(%s, %s)", (person, PASSWORD))
        cur.execute("SELECT drop_db_user(%s)", (person,))

    assert not role_exists(admin_db, NOTEBOOK)
    assert not role_exists(admin_db, person)


def test_a_service_role_gets_no_notebook(crudman_db):
    """The deployed engine is not a person, and a login defaulting into it would hand
    production's rights to whoever spawned the server."""
    with crudman_db.cursor() as cur:
        with pytest.raises(psycopg2.errors.RaiseException):
            cur.execute("SELECT create_notebook_login(%s, %s)", (SQLMESH_DB_USER, PASSWORD))
