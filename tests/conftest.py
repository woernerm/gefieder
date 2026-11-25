"""Shared fixtures for the integration tests.

The tests run against a throwaway stack run-tests.sh has already started, reached over
its published ports. Which ports and protocol depend on the profile (dev = plain HTTP,
production = HTTPS), passed in through the environment.
"""
import json
from contextlib import closing
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import psycopg2
import pytest


def podman(*args):
    """Run a podman command and return its stdout, raising on a non-zero exit."""
    return subprocess.run(
        ["podman", *args], capture_output=True, text=True, check=True,
    ).stdout


def crudman(script):
    """Run a shell script inside the crudman container and return its output."""
    return podman("exec", "crudman", "sh", "-c", script)


def django(python):
    """Run a line of Python against the admin panel's Django, inside its container."""
    return crudman(
        "cd /crudman/app && uv run --project /crudman python manage.py shell -c "
        f'"{python}"'
    )


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


REPO = Path(__file__).resolve().parents[1]


def declared(env_file):
    """The names an env file of the repository declares, e.g. runtime.env."""
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", (REPO / env_file).read_text(), re.MULTILINE))


def allowlisted(script):
    """The names in a script's VARS='${A} ${B}' envsubst allowlist."""
    text = (REPO / script).read_text()
    line = re.search(r"^\s*(?:QUADLET_)?VARS='([^']*)'", text, re.MULTILINE)
    assert line, f"{script} has no VARS allowlist"
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line.group(1)))


def poll(probe, timeout, interval=2, errors=()):
    """Call probe until it returns something truthy or timeout seconds have passed.

    A deadline rather than an attempt count, so the budget stays a wall-clock allowance on
    a slow machine, where each failing attempt itself takes longer.

    Args:
        probe: Called without arguments.
        timeout: Seconds to keep trying.
        interval: Seconds between attempts.
        errors: Exceptions that count as "not yet" rather than failing.

    Returns:
        The first truthy result, or the last one once the time is up.
    """
    deadline = time.time() + timeout
    while True:
        try:
            result = probe()
        except errors:
            result = None
        if result or time.time() >= deadline:
            return result
        time.sleep(interval)


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
PG_SUPERUSER_PASSWORD = os.environ["TEST_PG_SUPERUSER_PASSWORD"]
CRUDMAN_PASSWORD = os.environ["TEST_CRUDMAN_PASSWORD"]
SQLMESH_PASSWORD = os.environ["TEST_SQLMESH_PASSWORD"]

# Values taken from buildtime.env by run-tests.sh, so the suite tests the configured
# stack rather than the defaults.
APP_NAME = os.environ["APP_NAME"]
SUPERUSER_NAME = os.environ["SUPERUSER_NAME"]
PG_SUPERUSER_ROLE = os.environ["PG_SUPERUSER_ROLE"]

# The podman secrets, keyed by the component whose credential they hold. Read from the
# environment for the same reason as the role names: a deployment that renamed one around
# a secret the host already held is still the stack these tests point at.
SECRETS = {
    "superuser": os.environ["SECRET_SUPERUSER_PASSWORD"],
    "pg_superuser": os.environ["SECRET_PG_SUPERUSER_PASSWORD"],
    "crudman": os.environ["SECRET_CRUDMAN_PASSWORD"],
    "sqlmesh": os.environ["SECRET_SQLMESH_PASSWORD"],
    "grafana": os.environ["SECRET_GRAFANA_PASSWORD"],
    "django_key": os.environ["SECRET_DJANGO_KEY"],
    "oidc_client": os.environ["SECRET_OIDC_CLIENT"],
    "jupyter": os.environ["SECRET_JUPYTER"],
    "grafana_identity": os.environ["SECRET_GRAFANA_IDENTITY"],
}
CRUDMAN_PATH = os.environ["CRUDMAN_PATH"]
GRAFANA_PATH = os.environ["GRAFANA_PATH"]
MCP_PATH = os.environ["MCP_PATH"]
NOTEBOOK_PATH = os.environ["NOTEBOOK_PATH"]

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

# The staging layer each project's silver transform writes to before the UNION ALL into
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
# An address that reaches Grafana without a session: its own pages send an anonymous
# visitor to the admin panel's login, which would report the admin panel ready, not
# Grafana. Assets are served to anyone.
GRAFANA_PROBE = f"/{GRAFANA_PATH}/public/img/grafana_icon.svg"

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
              "jupyter", "proxy"]
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
    PG_SUPERUSER_ROLE: PG_SUPERUSER_PASSWORD,
    CRUDMAN_DB_USER: CRUDMAN_PASSWORD,
    SQLMESH_DB_USER: SQLMESH_PASSWORD,
    GRAFANA_DB_USER: GRAFANA_PASSWORD,
}


def sign_in(username=SUPERUSER_NAME, password=SUPERUSER_PASSWORD):
    """A browser signed in to the admin panel, as a client that follows redirects.

    The form is served by the admin itself; sso/views.login hands a POST to it, so the
    local credentials work whether or not single sign-on is configured. The caller closes
    the client.
    """
    client = httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=True, timeout=10)
    login = f"/{CRUDMAN_PATH}/login/"
    client.get(login)
    client.post(login, data={
        "csrfmiddlewaretoken": client.cookies["csrftoken"],
        "username": username,
        "password": password,
        "next": f"/{CRUDMAN_PATH}/",
    }, headers={"Referer": f"{BASE_URL}{login}"})
    return client


@pytest.fixture(scope="session")
def admin_session():
    """One browser session signed in as the superuser, for the whole run.

    The pages that are not the admin's own -- the documentation and the model versions page --
    are still behind its login, so a test that reads one needs a signed-in client rather
    than the anonymous ones above. Shared across modules, so a module that signs out or
    restarts the admin panel takes a client of its own from sign_in() instead.
    """
    client = sign_in()
    yield client
    client.close()


def connect_as(user, password=None, dbname=PG_DATABASE):
    """An autocommit connection as a role, with its secret unless a password is given."""
    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=dbname, user=user,
        password=DB_PASSWORDS[user] if password is None else password,
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
        self._conn = connect_as(user)

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
        # The last attempt outside poll, surfacing the error if it fails.
        self._conn = poll(
            lambda: connect_as(self._user), RESTART_TIMEOUT, errors=(psycopg2.OperationalError,)
        ) or connect_as(self._user)

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


@pytest.fixture(scope="module")
def grafana_api():
    """Grafana's API, as the superuser signed in to the admin panel."""
    client = sign_in()
    client.base_url = f"{BASE_URL}/{GRAFANA_PATH}"
    client.timeout = 30
    yield client
    client.close()


@pytest.fixture(scope="session")
def admin_db(connect):
    """A superuser connection, used by tests that must create objects."""
    return connect(PG_SUPERUSER_ROLE)


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


@pytest.fixture(scope="module")
def make_dropzones(crudman_db):
    """Factory creating a module's dropzones directly in the database, removed afterwards.

    Called with the upload method, the secret and name -> (checker, converter). Each call
    also creates "<method>-suite-browser", a browser dropzone whose name must not work as a
    login on the method's endpoint. Returns name -> id.
    """
    created = []

    def _make(method, secret, dropzones):
        rows = [(name, method, *functions) for name, functions in dropzones.items()]
        rows.append((f"{method}-suite-browser", "browser", "", ""))
        ids = {}
        with crudman_db.cursor() as cur:
            for name, upload_method, checker, converter in rows:
                cur.execute(
                    """
                    INSERT INTO crudman.dropzones_dropzone
                        (name, description, upload_method, file_format, checker,
                         converter, default_validity, token, secret, require_login,
                         enabled, created_at)
                    VALUES (%s, '', %s, '', %s, %s, 'until_replaced', %s, %s, true,
                            true, now())
                    RETURNING id
                    """,
                    (name, upload_method, checker, converter, str(uuid.uuid4()), secret),
                )
                ids[name] = cur.fetchone()[0]
        created.extend(ids.values())
        return ids

    yield _make
    # Raw cleanup, no Django signals: the stack and its volumes are throwaway.
    with crudman_db.cursor() as cur:
        cur.execute(
            "DELETE FROM crudman.dropzones_uploadfile WHERE upload_id IN "
            "(SELECT id FROM crudman.dropzones_upload WHERE dropzone_id = ANY(%s))",
            (created,),
        )
        cur.execute(
            "DELETE FROM crudman.dropzones_upload WHERE dropzone_id = ANY(%s)", (created,)
        )
        cur.execute("DELETE FROM crudman.dropzones_dropzone WHERE id = ANY(%s)", (created,))


def dropzone_uploads(db, dropzone_id):
    """The (upload id, stored file paths) pairs recorded for a dropzone."""
    with db.cursor() as cur:
        cur.execute(
            """
            SELECT u.id, array_agg(f.file ORDER BY f.file)
            FROM crudman.dropzones_upload u
            JOIN crudman.dropzones_uploadfile f ON f.upload_id = u.id
            WHERE u.dropzone_id = %s
            GROUP BY u.id ORDER BY u.id
            """,
            (dropzone_id,),
        )
        return cur.fetchall()


def stack_not_ready(reason, units):
    """End the run, naming what is missing and what the units responsible logged.

    An exit rather than a failure: pytest repeats a failed session fixture as the error of
    every single test, which buries the one line that matters.
    """
    for unit in units:
        log = subprocess.run(
            ["journalctl", "--user", "-u", f"{unit}.service", "-n", "40", "--no-pager"],
            capture_output=True, text=True,
        ).stdout
        reason += f"\n\n--- {unit} ---\n{log}"
    pytest.exit(f"stack did not become ready: {reason}", returncode=1)


@pytest.fixture(scope="session", autouse=True)
def wait_for_stack():
    """Block until both apps respond and the engine is done with its first plan.

    The suite starts as soon as the stack is up, while that plan still creates schemas and
    grants in the catalog the access-control tests change too. A failed plan counts as done:
    the tests that need its tables name the failure better than this would.
    """
    deadline = time.time() + STARTUP_TIMEOUT
    seen = None

    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=True, timeout=5) as client:
        def answers(target):
            nonlocal seen
            try:
                seen = client.get(target).status_code
            except httpx.HTTPError as error:
                seen = repr(error)
            return seen == 200

        for target, unit in [(CRUDMAN_LOGIN, "crudman"), (GRAFANA_PROBE, "grafana")]:
            if not poll(lambda: answers(target), deadline - time.time()):
                stack_not_ready(f"{BASE_URL}{target} last answered {seen}", [unit, "proxy"])

    def first_plan_done():
        nonlocal seen
        try:
            with closing(connect_as(PG_SUPERUSER_ROLE)) as conn, conn.cursor() as cur:
                cur.execute("SELECT status, message FROM crudman.system_deployment "
                            "ORDER BY created_on DESC LIMIT 1")
                seen = cur.fetchone()
        except psycopg2.Error as error:
            seen = repr(error)
            return False
        return seen is not None and seen[0] in ("succeeded", "failed")

    seen = None
    if not poll(first_plan_done, deadline - time.time()):
        stack_not_ready(f"the first sqlmesh plan never finished, last seen {seen}",
                        ["crudman", "sqlmesh"])
