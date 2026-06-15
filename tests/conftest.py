"""Shared fixtures for the Gefieder integration tests.

The tests run against a throwaway stack that run-tests.sh has already started. The
stack is reached over the published ports; which ports and protocol depend on the
profile (dev = plain HTTP, production = HTTPS), passed in via environment variables.
"""
import os
import time

import httpx
import psycopg2
import pytest


def allowed(conn, sql):
    """Assert that running sql as this role succeeds (the role has the privilege)."""
    with conn.cursor() as cur:
        cur.execute(sql)


def denied(conn, sql):
    """Assert that running sql as this role is rejected for lack of privilege."""
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(sql)

PROFILE = os.environ.get("GEFIEDER_PROFILE", "dev")
BASE_URL = os.environ["GEFIEDER_BASE_URL"]            # e.g. http://localhost:8080
HTTP_BASE_URL = os.environ["GEFIEDER_HTTP_BASE_URL"]  # the plain-HTTP base, for the redirect test
PG_PORT = os.environ.get("GEFIEDER_PG_PORT", "5432")
GRAFANA_PASSWORD = os.environ["GEFIEDER_GRAFANA_PASSWORD"]
SUPERUSER_PASSWORD = os.environ["GEFIEDER_SUPERUSER_PASSWORD"]
CRUDMAN_PASSWORD = os.environ["GEFIEDER_CRUDMAN_PASSWORD"]
SQLMESH_PASSWORD = os.environ["GEFIEDER_SQLMESH_PASSWORD"]

# Values taken from the .env file by run-tests.sh, so the suite tests the configured
# stack rather than the defaults.
APP_NAME = os.environ["APP_NAME"]
SUPERUSER_NAME = os.environ["SUPERUSER_NAME"]
CRUDMAN_PATH = os.environ["CRUDMAN_PATH"]
GRAFANA_PATH = os.environ["GRAFANA_PATH"]

# The URL paths the apps are served under, derived from the configured base paths.
CRUDMAN_LOGIN = f"/{CRUDMAN_PATH}/login/"
GRAFANA_LOGIN = f"/{GRAFANA_PATH}/login"

# The names of the containers that make up the stack.
CONTAINERS = ["postgresql", "crudman", "sqlmesh", "grafana", "proxy"]

# In the production profile the proxy serves a self-signed certificate, so TLS
# verification is disabled for the test run.
VERIFY_TLS = False


def pytest_configure(config):
    # Deselect production-only assertions unless we run the production profile.
    if PROFILE != "production":
        setattr(config.option, "markexpr",
                "not production" if not config.option.markexpr
                else f"({config.option.markexpr}) and not production")


@pytest.fixture(scope="session")
def http():
    """An HTTP client that does not follow redirects (so we can assert on them)."""
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS,
                      follow_redirects=False, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def http_follow():
    """An HTTP client that follows redirects, for fetching final pages and assets."""
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS,
                      follow_redirects=True, timeout=10) as client:
        yield client


# The login password of every database role the access-control tests connect as.
DB_PASSWORDS = {
    SUPERUSER_NAME: SUPERUSER_PASSWORD,
    "crudman": CRUDMAN_PASSWORD,
    "sqlmesh": SQLMESH_PASSWORD,
    "grafana": GRAFANA_PASSWORD,
}


def _connect(user):
    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname="postgres",
        user=user, password=DB_PASSWORDS[user],
    )
    conn.autocommit = True
    return conn


@pytest.fixture(scope="session")
def connect():
    """Factory yielding a database connection for a given role, cleaned up at the end."""
    conns = []

    def _factory(user):
        conn = _connect(user)
        conns.append(conn)
        return conn

    yield _factory
    for conn in conns:
        conn.close()


@pytest.fixture(scope="session")
def db(connect):
    """A psycopg2 connection as the read-only grafana role."""
    return connect("grafana")


@pytest.fixture(scope="session")
def admin_db(connect):
    """A superuser connection, used by tests that must create objects."""
    return connect(SUPERUSER_NAME)


@pytest.fixture(scope="session")
def crudman_db(connect):
    """A connection as the crudman application role."""
    return connect("crudman")


@pytest.fixture(scope="session")
def sqlmesh_db(connect):
    """A connection as the sqlmesh analytics role."""
    return connect("sqlmesh")


@pytest.fixture(scope="session")
def grafana_db(connect):
    """A connection as the read-only grafana role (alias of db, for clarity)."""
    return connect("grafana")


@pytest.fixture(scope="session", autouse=True)
def wait_for_stack():
    """Block until both apps respond and sqlmesh has created its schema.

    The apps are gated on their HTTP endpoints. The sqlmesh schema is created by the
    engine's first `sqlmesh plan` at runtime (not by database init), so the schema and
    access-control tests would race a slow first plan; wait for it here too.
    """
    deadline = time.time() + 180
    targets = [CRUDMAN_LOGIN, GRAFANA_LOGIN]
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS,
                      follow_redirects=True, timeout=5) as client:
        for target in targets:
            while True:
                try:
                    if client.get(target).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.time() > deadline:
                    pytest.fail(f"stack did not become ready: {target} unreachable")
                time.sleep(2)

    while True:
        try:
            conn = _connect(SUPERUSER_NAME)
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'sqlmesh'")
                ready = cur.fetchone() is not None
            conn.close()
            if ready:
                break
        except psycopg2.Error:
            pass
        if time.time() > deadline:
            pytest.fail("stack did not become ready: sqlmesh schema not created")
        time.sleep(2)
