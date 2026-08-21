"""Shared fixtures for the integration tests.

The tests run against a throwaway stack that run-tests.sh has already started. The
stack is reached over the published ports; which ports and protocol depend on the
profile (dev = plain HTTP, production = HTTPS), passed in via environment variables.
"""
import json
import os
import subprocess
import time

import httpx
import psycopg2
import pytest


def podman(*args):
    """Run a podman command and return its stdout, raising on a non-zero exit."""
    return subprocess.run(
        ["podman", *args], capture_output=True, text=True, check=True,
    ).stdout


def inspect_container(name):
    """Return the `podman inspect` object for a container, or None if it does not exist.

    Scoped to containers with an explicit --type: bare `podman inspect` falls back to
    images when no container matches, and every service here shares its name with its
    image, so a container that is mid-recreate would otherwise return the image manifest
    (which has no "State") instead of signalling absence.
    """
    result = subprocess.run(
        ["podman", "inspect", "--type", "container", name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)[0]


def volume_mountpoint(volume):
    """Return the host filesystem path backing a named podman volume."""
    return podman("volume", "inspect", volume, "-f", "{{.Mountpoint}}").strip()


def allowed(conn, sql):
    """Assert that running sql as this role succeeds (the role has the privilege)."""
    with conn.cursor() as cur:
        cur.execute(sql)


def denied(conn, sql):
    """Assert that running sql as this role is rejected for lack of privilege."""
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(sql)

PROFILE = os.environ.get("TEST_PROFILE", "dev")
BASE_URL = os.environ["TEST_BASE_URL"]            # e.g. http://localhost:8080
HTTP_BASE_URL = os.environ["TEST_HTTP_BASE_URL"]  # the plain-HTTP base, for the redirect test
PG_PORT = os.environ.get("TEST_PG_PORT", "5432")
PG_DATABASE = os.environ.get("TEST_PG_DATABASE", "postgres")
SFTP_PORT = int(os.environ.get("TEST_SFTP_PORT", "2222"))
FLIGHT_PORT = int(os.environ.get("TEST_FLIGHT_PORT", "8815"))
GRAFANA_PASSWORD = os.environ["TEST_GRAFANA_PASSWORD"]
SUPERUSER_PASSWORD = os.environ["TEST_SUPERUSER_PASSWORD"]
CRUDMAN_PASSWORD = os.environ["TEST_CRUDMAN_PASSWORD"]
SQLMESH_PASSWORD = os.environ["TEST_SQLMESH_PASSWORD"]

# Values taken from buildtime.env by run-tests.sh, so the suite tests the configured
# stack rather than the defaults.
APP_NAME = os.environ["APP_NAME"]
SUPERUSER_NAME = os.environ["SUPERUSER_NAME"]

# The names of the podman secrets, keyed by the component whose credential they hold. The
# suite reads them from the environment for the same reason it reads the role names: a
# deployment that had to rename one around a secret the host already held is still the
# stack these tests are pointed at.
SECRETS = {
    "superuser": os.environ["SECRET_SUPERUSER_PASSWORD"],
    "crudman": os.environ["SECRET_CRUDMAN_PASSWORD"],
    "sqlmesh": os.environ["SECRET_SQLMESH_PASSWORD"],
    "grafana": os.environ["SECRET_GRAFANA_PASSWORD"],
    "django_key": os.environ["SECRET_DJANGO_KEY"],
    "oidc_client": os.environ["SECRET_OIDC_CLIENT"],
}
CRUDMAN_PATH = os.environ["CRUDMAN_PATH"]
GRAFANA_PATH = os.environ["GRAFANA_PATH"]

# The database login roles the init scripts created, and the prefix on the roles that
# belong to people. The access-control checks connect as these and assert their boundary,
# so they have to be the configured names rather than the ones buildtime.env ships with.
CRUDMAN_DB_USER = os.environ["CRUDMAN_DB_USER"]
SQLMESH_DB_USER = os.environ["SQLMESH_DB_USER"]
GRAFANA_DB_USER = os.environ["GRAFANA_DB_USER"]
DB_ROLE_PREFIX = os.environ["DB_ROLE_PREFIX"]

# The Django group each identity-provider rank grants, and the medallion schemas the init
# scripts created. Same reason: the suite has to check the configured stack, and a schema
# name spelled out here would assert the default instead of what was built.
SSO_GROUP_PREFIX = os.environ["SSO_GROUP_PREFIX"]
BRONZE_SCHEMA_PREFIX = os.environ["BRONZE_SCHEMA_PREFIX"]
SILVER_SCHEMA = os.environ["SILVER_SCHEMA"]
GOLD_SCHEMA = os.environ["GOLD_SCHEMA"]

# The staging layer each tenant's silver transform writes to before the thin UNION ALL into
# SILVER_SCHEMA. Not a build-time setting, because nothing in the deployment names it: the
# init scripts grant on it through the SILVER_SCHEMA prefix match, and the only place it is
# written out is the SQLMesh models. So it is derived here, where the two tests that care
# about it live -- test_access_control asserts grafana cannot see it, and
# test_medallion_schemas asserts a shipped model still writes to it, which is what catches
# the derivation going stale.
SILVER_STAGING_SCHEMA = f"{SILVER_SCHEMA}_staging"

# The server-statistics schema name and the host-side collector run-tests.sh installed,
# so the server-stats tests can trigger a real sample and read its rows back.
SERVER_STATS_SCHEMA = os.environ.get("TEST_SERVER_STATS_SCHEMA", "server_stats")
COLLECTOR = os.environ.get("TEST_COLLECTOR", "")

# The URL paths the apps are served under, derived from the configured base paths.
CRUDMAN_LOGIN = f"/{CRUDMAN_PATH}/login/"
GRAFANA_LOGIN = f"/{GRAFANA_PATH}/login"

# The stand-in identity provider run-tests.sh starts inside the pod, and the configuration
# directory holding the runtime.env the services read their settings from.
OIDC_ISSUER = os.environ.get("TEST_OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("TEST_OIDC_CLIENT_ID", "")
APP_CONFIG_DIR = os.environ.get("TEST_APP_CONFIG_DIR", "")

# The names of the containers that make up the stack.
CONTAINERS = ["postgresql", "crudman", "sftp", "flight", "sqlmesh", "grafana",
              "proxy"]

# The systemd unit of each service, whose journal holds that service's log. Every service
# logs to stdout/stderr only; podman forwards the stream to journald, which persists,
# rotates and size-caps it.
LOGGING_UNITS = [
    "postgresql", "crudman", "sftp", "flight", "sqlmesh", "grafana", "proxy",
]

# In the production profile the proxy serves a self-signed certificate, so TLS
# verification is disabled for the test run.
VERIFY_TLS = False

# How long to allow for a service to come back after being killed or restarted, and for
# the whole stack to become ready at session start. The resilience checks wait on an
# observable state change rather than on a fixed delay, so these are only upper bounds
# for a machine that never gets there; raise TEST_RESTART_TIMEOUT on a very slow host.
RESTART_TIMEOUT = int(os.environ.get("TEST_RESTART_TIMEOUT", "180"))
STARTUP_TIMEOUT = int(os.environ.get("TEST_STARTUP_TIMEOUT", "300"))


def pytest_configure(config):
    # Deselect production-only assertions unless we run the production profile.
    if PROFILE != "production":
        setattr(config.option, "markexpr",
                "not production" if not config.option.markexpr
                else f"({config.option.markexpr}) and not production")


# trust_env=False on every client below: on a company network the proxy variables are set
# in the environment, and httpx would send the requests to the company proxy rather than to
# the stack on localhost, unless no_proxy happens to name it.


@pytest.fixture(scope="session")
def http():
    """An HTTP client that does not follow redirects (so we can assert on them)."""
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=False, timeout=10) as client:
        yield client


@pytest.fixture(scope="session")
def http_follow():
    """An HTTP client that follows redirects, for fetching final pages and assets."""
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=True, timeout=10) as client:
        yield client


# The login password of every database role the access-control tests connect as.
DB_PASSWORDS = {
    SUPERUSER_NAME: SUPERUSER_PASSWORD,
    CRUDMAN_DB_USER: CRUDMAN_PASSWORD,
    SQLMESH_DB_USER: SQLMESH_PASSWORD,
    GRAFANA_DB_USER: GRAFANA_PASSWORD,
}


def _connect(user):
    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=PG_DATABASE,
        user=user, password=DB_PASSWORDS[user],
    )
    conn.autocommit = True
    return conn


class _ReconnectingConnection:
    """A psycopg2 connection wrapper that reopens itself if the backend has gone away.

    The role connections are session-scoped and shared across tests. A test that restarts
    postgresql (test_resilience) kills every backend, so a later test reaching for one of
    these connections would otherwise hit "server closed the connection unexpectedly".
    This wrapper checks the connection before each ``cursor()`` call and reconnects when it
    is closed or broken, so every holder of the same object transparently gets a live
    connection without having to re-fetch it.
    """

    def __init__(self, user):
        self._user = user
        self._conn = _connect(user)

    def _ensure_alive(self):
        # psycopg2 sets .closed != 0 once it notices the backend is gone; a still-open
        # handle is probed with a trivial query so a server restart is detected eagerly.
        if self._conn.closed:
            self._reconnect()
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg2.Error:
            self._reconnect()

    def _reconnect(self):
        # Retry until the freshly restarted server accepts connections again. Bounded by
        # a deadline rather than an attempt count so the budget stays a wall-clock
        # allowance on a slow machine, where each failing attempt itself takes longer and
        # would otherwise burn through the attempts well before the server is back.
        deadline = time.time() + RESTART_TIMEOUT
        while time.time() < deadline:
            try:
                self._conn = _connect(self._user)
                return
            except psycopg2.OperationalError:
                time.sleep(2)
        self._conn = _connect(self._user)  # last attempt, surfacing the error if it fails

    def cursor(self, *args, **kwargs):
        self._ensure_alive()
        return self._conn.cursor(*args, **kwargs)

    def close(self):
        self._conn.close()

    def __getattr__(self, name):
        # Delegate any other attribute access (e.g. .autocommit) to the live connection.
        return getattr(self._conn, name)


@pytest.fixture(scope="session")
def connect():
    """Factory yielding a database connection for a given role, cleaned up at the end.

    Each role gets one shared, self-healing connection (see ``_ReconnectingConnection``),
    so tests that run after a postgresql restart still receive a live connection.
    """
    conns = {}

    def _factory(user):
        if user not in conns:
            conns[user] = _ReconnectingConnection(user)
        return conns[user]

    yield _factory
    for conn in conns.values():
        conn.close()


@pytest.fixture(scope="session")
def db(connect):
    """A psycopg2 connection as the read-only grafana role."""
    return connect(GRAFANA_DB_USER)


@pytest.fixture(scope="session")
def admin_db(connect):
    """A superuser connection, used by tests that must create objects."""
    return connect(SUPERUSER_NAME)


@pytest.fixture(scope="session")
def crudman_db(connect):
    """A connection as the crudman application role."""
    return connect(CRUDMAN_DB_USER)


@pytest.fixture(scope="session")
def sqlmesh_db(connect):
    """A connection as the sqlmesh analytics role."""
    return connect(SQLMESH_DB_USER)


@pytest.fixture(scope="session")
def grafana_db(connect):
    """A connection as the read-only grafana role (alias of db, for clarity)."""
    return connect(GRAFANA_DB_USER)


@pytest.fixture(scope="session", autouse=True)
def wait_for_stack():
    """Block until both apps respond and sqlmesh has created its schema.

    The apps are gated on their HTTP endpoints. The sqlmesh schema is created by the
    engine's first `sqlmesh plan` at runtime (not by database init), so the schema and
    access-control tests would race a slow first plan; wait for it here too.
    """
    deadline = time.time() + STARTUP_TIMEOUT
    targets = [CRUDMAN_LOGIN, GRAFANA_LOGIN]
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
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
