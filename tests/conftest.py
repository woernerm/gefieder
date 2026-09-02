"""Shared fixtures for the integration tests.

The tests run against a throwaway stack run-tests.sh has already started, reached over
its published ports. Which ports and protocol depend on the profile (dev = plain HTTP,
production = HTTPS), passed in through the environment.
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

    Scoped with --type: bare `podman inspect` falls back to images, and every service
    shares its name with its image, so a container mid-recreate would return the image
    manifest instead of signalling absence.
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

# The podman secrets, keyed by the component whose credential they hold. Read from the
# environment for the same reason as the role names: a deployment that renamed one around
# a secret the host already held is still the stack these tests point at.
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
MCP_PATH = os.environ["MCP_PATH"]

# The database login roles the init scripts created, and the prefix on the roles that
# belong to people. The access-control checks connect as these, so they have to be the
# configured names rather than the ones buildtime.env ships with.
CRUDMAN_DB_USER = os.environ["CRUDMAN_DB_USER"]
SQLMESH_DB_USER = os.environ["SQLMESH_DB_USER"]
GRAFANA_DB_USER = os.environ["GRAFANA_DB_USER"]
DB_USER_PREFIX = os.environ["DB_USER_PREFIX"]

# The prefix each rank is named behind -- Django group and database group role alike --
# and the medallion schemas. Same reason: a name spelled out here would assert the default
# rather than what was built.
ROLE_PREFIX = os.environ["ROLE_PREFIX"]
BRONZE_SCHEMA_PREFIX = os.environ["BRONZE_SCHEMA_PREFIX"]
SILVER_SCHEMA = os.environ["SILVER_SCHEMA"]
GOLD_SCHEMA = os.environ["GOLD_SCHEMA"]

# The staging layer each tenant's silver transform writes to before the UNION ALL into
# SILVER_SCHEMA. Not a build-time setting: the init scripts grant on it through the
# SILVER_SCHEMA prefix match, and only the SQLMesh models write it out. Derived here, and
# test_medallion_schemas is what catches the derivation going stale.
SILVER_STAGING_SCHEMA = f"{SILVER_SCHEMA}_staging"

# The server-statistics schema and the host-side collector run-tests.sh installed, so the
# tests can trigger a real sample and read its rows back.
SERVER_STATS_SCHEMA = os.environ.get("TEST_SERVER_STATS_SCHEMA", "server_stats")
COLLECTOR = os.environ.get("TEST_COLLECTOR", "")

# The URL paths the apps are served under, derived from the configured base paths.
CRUDMAN_LOGIN = f"/{CRUDMAN_PATH}/login/"
GRAFANA_LOGIN = f"/{GRAFANA_PATH}/login"

# The stand-in identity provider run-tests.sh starts inside the pod, and the directory
# holding the runtime.env the services read their settings from.
OIDC_ISSUER = os.environ.get("TEST_OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.environ.get("TEST_OIDC_CLIENT_ID", "")
APP_CONFIG_DIR = os.environ.get("TEST_APP_CONFIG_DIR", "")

# The names of the containers that make up the stack. Each is also the systemd unit whose
# journal holds that service's log: every service logs to stdout/stderr only, and podman
# forwards the stream to journald. One list, so a service added to the stack cannot reach
# the startup checks while the logging checks silently skip it.
CONTAINERS = ["postgresql", "crudman", "sftp", "flight", "sqlmesh", "grafana", "grafana_mcp",
              "proxy"]
LOGGING_UNITS = CONTAINERS

# In the production profile the proxy serves a self-signed certificate, so TLS
# verification is disabled for the test run.
VERIFY_TLS = False

# How long a service gets to come back after a restart, and the stack to become ready at
# session start. The checks wait on an observable state change, so these are upper bounds
# only; raise TEST_RESTART_TIMEOUT on a very slow host.
RESTART_TIMEOUT = int(os.environ.get("TEST_RESTART_TIMEOUT", "180"))
STARTUP_TIMEOUT = int(os.environ.get("TEST_STARTUP_TIMEOUT", "300"))


def pytest_configure(config):
    # Deselect production-only assertions unless we run the production profile.
    if PROFILE != "production":
        setattr(config.option, "markexpr",
                "not production" if not config.option.markexpr
                else f"({config.option.markexpr}) and not production")


# trust_env=False on every client below: with the proxy variables set, httpx would send
# the requests to the company proxy rather than to the stack on localhost.


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

    The role connections are session-scoped and shared, and test_resilience restarts
    postgresql, killing every backend. Checking before each ``cursor()`` call means every
    holder of the object gets a live connection without re-fetching it.
    """

    def __init__(self, user):
        self._user = user
        self._conn = _connect(user)

    def _ensure_alive(self):
        # psycopg2 sets .closed once it notices the backend is gone; a still-open handle
        # is probed so a server restart is detected eagerly.
        if self._conn.closed:
            self._reconnect()
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg2.Error:
            self._reconnect()

    def _reconnect(self):
        # A deadline rather than an attempt count, so the budget stays a wall-clock
        # allowance on a slow machine, where each failing attempt itself takes longer.
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
        # Anything else (.autocommit, say) goes to the live connection.
        return getattr(self._conn, name)


@pytest.fixture(scope="session")
def connect():
    """Factory yielding a database connection for a given role, cleaned up at the end.

    One shared, self-healing connection per role, so a test after a postgresql restart
    still receives a live one.
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

    The schema comes from the engine's first `sqlmesh plan` at runtime rather than from
    database init, so the schema and access-control tests would otherwise race it.
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
