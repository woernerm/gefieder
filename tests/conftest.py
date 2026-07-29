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


def inspect(target):
    """Return the parsed `podman inspect` object for a container or other resource."""
    return json.loads(podman("inspect", target))[0]


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


def mount_in_container(container, volume):
    """Return the destination path the named volume is mounted at inside the container."""
    for m in inspect(container).get("Mounts", []):
        if m.get("Name") == volume:
            return m["Destination"]
    raise AssertionError(f"{volume} is not mounted in {container}")


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
SFTP_PORT = int(os.environ.get("TEST_SFTP_PORT", "2222"))
FLIGHT_PORT = int(os.environ.get("TEST_FLIGHT_PORT", "8815"))
GRAFANA_PASSWORD = os.environ["TEST_GRAFANA_PASSWORD"]
SUPERUSER_PASSWORD = os.environ["TEST_SUPERUSER_PASSWORD"]
CRUDMAN_PASSWORD = os.environ["TEST_CRUDMAN_PASSWORD"]
SQLMESH_PASSWORD = os.environ["TEST_SQLMESH_PASSWORD"]

# Values taken from the .env file by run-tests.sh, so the suite tests the configured
# stack rather than the defaults.
APP_NAME = os.environ["APP_NAME"]
SUPERUSER_NAME = os.environ["SUPERUSER_NAME"]
CRUDMAN_PATH = os.environ["CRUDMAN_PATH"]
GRAFANA_PATH = os.environ["GRAFANA_PATH"]

# The server-statistics schema name and the host-side collector run-tests.sh installed,
# so the server-stats tests can trigger a real sample and read its rows back.
SERVER_STATS_SCHEMA = os.environ.get("TEST_SERVER_STATS_SCHEMA", "server_stats")
COLLECTOR = os.environ.get("TEST_COLLECTOR", "")

# The URL paths the apps are served under, derived from the configured base paths.
CRUDMAN_LOGIN = f"/{CRUDMAN_PATH}/login/"
GRAFANA_LOGIN = f"/{GRAFANA_PATH}/login"

# The names of the containers that make up the stack.
CONTAINERS = ["postgresql", "crudman", "sftp", "flight", "sqlmesh", "grafana",
              "proxy"]

# The systemd unit that owns the pod (the quadlet file is named main.pod).
POD_SERVICE = "main-pod.service"

# The named data volumes the quadlets declare: one per service, plus the uploads
# volume crudman and sqlmesh share for the dropzones files.
DATA_VOLUMES = [
    "postgresql_data", "grafana_data", "crudman_data", "sftp_data", "sqlmesh_data",
    "proxy_data", "uploads_data",
]

# Where each service writes its persistent log, as (container, volume, path-in-volume).
# crudman/sftp/flight/sqlmesh/proxy tee their entrypoint output to a file the rootless
# user owns (sftp and flight run the crudman application, so their logs share crudman's
# volume); postgresql and grafana are configured to log into a subdir of their data
# volume.
PERSISTENT_LOGS = [
    ("crudman", "crudman_data", "crudman.log"),
    ("sftp", "crudman_data", "sftp.log"),
    ("flight", "crudman_data", "flight.log"),
    ("sqlmesh", "sqlmesh_data", "sqlmesh.log"),
    ("proxy", "proxy_data", "proxy.log"),
    ("postgresql", "postgresql_data", "log"),   # directory of dated log files
    ("grafana", "grafana_data", "log/grafana.log"),
]

# The services whose entrypoint tees the log as the rootless user (uid 0 in-container,
# mapped to the host user), so the log file is owned by that user without `podman
# unshare`. postgresql/grafana run as a non-root in-container user, so their files land
# on a mapped subuid instead and are excluded from the ownership assertion.
USER_OWNED_LOGS = ["crudman", "sftp", "flight", "sqlmesh", "proxy"]

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
    deadline = time.time() + STARTUP_TIMEOUT
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
