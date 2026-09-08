"""SQLMesh project configuration.

Python rather than YAML: the same project is loaded from two places with different
connection settings, and a Python config is evaluated when SQLMesh reads it, so
`sqlmesh plan` does the right thing both in the container and on a developer's machine.

SQLMesh loads at most one config per directory and refuses to start if a config.yaml sits
next to this file. Do not reintroduce one.
"""

import getpass
import os
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from sqlmesh.core.config import (
    Config,
    DuckDBConnectionConfig,
    GatewayConfig,
    ModelDefaultsConfig,
    PostgresConnectionConfig,
)
from sqlmesh.core.config.connection import DuckDBAttachOptions
from sqlmesh.core.config.linter import LinterConfig

SECRET_PATH = Path(
    "/run/secrets", os.environ.get("SECRET_SQLMESH_PASSWORD", "sqlmesh_password")
)
"""Where the podman secret holding the deployed engine's password is mounted."""

IN_CONTAINER = SECRET_PATH.exists()
"""Whether this is the deployed engine rather than a developer's checkout.

The secret is mounted only in the container, so unlike a hostname or an environment
variable its presence cannot accidentally be true elsewhere.
"""

CACHE = Path.home() / ".cache" / "sqlmesh" / "password.json"
"""Where a fetched password waits until it expires, so one run in ten fetches.

Under the home directory rather than beside the project: it is this machine's credential,
not part of the checkout, and a clone copied elsewhere must not carry it.
"""

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
"""Where plain http is the dev stack on this machine rather than a network hop."""


def fetch_password(url: str, token: str) -> str:
    """Get a database password from the admin panel, or reuse the cached one.

    Args:
        url: The admin panel's base address, from server.env.
        token: The token its owner created there.

    Returns:
        The password, cached under the home directory until it expires.

    Raises:
        ValueError: No token, or the panel refused it.
    """
    import json
    import urllib.request
    from datetime import datetime, timezone

    if not token:
        raise ValueError(
            "SQLMESH_TOKEN is not set. Create one in the admin panel under Database "
            "access and write it to sqlmesh/.env, which is never committed."
        )

    # A cached password is reused until it is nearly spent, a plan being long enough that
    # one expiring mid-run would fail halfway.
    if CACHE.exists():
        cached = json.loads(CACHE.read_text())
        expires = datetime.fromisoformat(cached["expires_at"])
        if cached.get("url") == url and expires - datetime.now(timezone.utc) > timedelta(
            minutes=5
        ):
            return cached["db_password"]

    # Https everywhere but a stack on this machine, where dev.sh serves plain http and
    # nothing leaves the loopback. Anywhere else the token would cross a network in the
    # clear, so this refuses rather than warning.
    parsed = urlparse(url)
    if parsed.scheme != "https" and parsed.hostname not in LOCAL_HOSTS:
        raise ValueError(
            f"{url} is not https. The token would cross the network in the clear; "
            "point SQLMESH_CRUDMAN_URL at the https address."
        )

    request = urllib.request.Request(
        f"{url.rstrip('/')}/dbusers/password/",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise ValueError(f"{url} refused the token ({error.code}): {detail}") from error

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({**answer, "url": url}))
    # Readable by nobody else: it holds a working credential until it expires.
    CACHE.chmod(0o600)
    return answer["db_password"]


if IN_CONTAINER:
    # The quadlet sets these; the pod shares one network namespace, so the database is
    # on localhost. The password comes from the secret rather than the environment, so
    # `podman exec sqlmesh sqlmesh ...` works too -- the wrapper on that container's PATH
    # settles the project path and the log directory.
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ.get("POSTGRES_DB", "postgres")
    password = SECRET_PATH.read_text().strip()
    # The deployed engine owns production, so it keeps the shared service role.
    user = os.environ.get("POSTGRES_USER", "sqlmesh")
else:
    # Over the network on a developer's machine, on the port the pod publishes.
    #
    # Two ways to arrive here. A clone of the models repository carries server.env, which
    # the system wrote when it created that repository, so cloning is the whole setup. A
    # checkout of the gefieder repository has no such file and the same values live in the
    # two env files beside the project. Either way an exported variable wins, which is how
    # one checkout is pointed at a second installation.
    project = Path(__file__).resolve().parent
    server = dotenv_values(project / "server.env")
    if not server:
        repo_root = project.parent
        runtime_env = dotenv_values(repo_root / "runtime.env")
        buildtime_env = dotenv_values(repo_root / "buildtime.env")
        server = {
            # SERVER_NAME names a local development stack as well as a server.
            "SQLMESH_HOST": runtime_env["SERVER_NAME"],
            "SQLMESH_PORT": runtime_env.get("PG_PORT") or "5432",
            # A build-time setting; the quadlet fills POSTGRES_DB from the same value.
            "SQLMESH_DATABASE": buildtime_env["PG_DATABASE"],
            "SQLMESH_USER_PREFIX": buildtime_env.get("DB_USER_PREFIX", "gf_"),
            # Where the token is exchanged for a password. Http only because this branch
            # is a checkout beside a local stack; a deployment writes the https address
            # into server.env instead.
            "SQLMESH_CRUDMAN_URL": (
                f"http://{runtime_env['SERVER_NAME']}"
                f"/{buildtime_env.get('CRUDMAN_PATH', 'crudman')}"
            ),
        }

    def setting(name: str) -> str:
        """One connection setting, the environment overruling the file."""
        return os.environ.get(name) or server[name]

    host = setting("SQLMESH_HOST")
    port = int(setting("SQLMESH_PORT"))
    database = setting("SQLMESH_DATABASE")
    # Developers connect as themselves, so the shared sqlmesh secret never leaves the
    # server, a query stays traceable to a person and a departure is one role disabled.
    # The name is derived as crudman derives it when provisioning (dbusers.utils.
    # role_name_for). Someone whose local account is named differently overrides it with
    # SQLMESH_USER.
    role_prefix = setting("SQLMESH_USER_PREFIX")
    user = os.environ.get("SQLMESH_USER") or (
        role_prefix
        + re.sub(r"[^a-z0-9]+", "_", getpass.getuser().strip().lower()).strip("_")
    )[:50]

    # SQLMesh loads sqlmesh/.env before importing this file, so an exported variable and
    # the gitignored file both work. A password set by hand still wins, which is what
    # keeps a stack without the admin panel reachable usable.
    password = os.environ.get("SQLMESH_PASSWORD") or fetch_password(
        setting("SQLMESH_CRUDMAN_URL"), os.environ.get("SQLMESH_TOKEN", "")
    )


def attach_path(**settings: object) -> str:
    """Build the libpq connection string DuckDB attaches PostgreSQL with.

    Two layers of quoting have to survive each other, and skipping either works until a
    password is not a tame hex string.

    Args:
        **settings: The libpq keywords and their values, e.g. dbname, host, password.

    Returns:
        The connection string, each value single-quoted for libpq because a password may
        hold a space, and the whole escaped for the literal SQLMesh wraps it in
        (``ATTACH '<path>'``), where DuckDB reads a doubled quote as one.
    """

    def quoted(value: object) -> str:
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    conninfo = " ".join(f"{key}={quoted(value)}" for key, value in settings.items())
    return conninfo.replace("'", "''")


# Read back from the image rather than listed again, so the gateway loads what is on
# disk. Empty on a developer's machine, where DuckDB downloads what a model asks for.
duckdb_extensions = [e for e in os.environ.get("DUCKDB_EXTENSIONS", "").split(",") if e]

config = Config(
    # The deployed project is a read-only checkout, so SQLMesh's parsed-model cache cannot
    # live beside it as it does on a developer's machine. Only in the container: a
    # developer keeps the cache with the project, where "sqlmesh clean" expects it.
    **({"cache_dir": "/tmp/sqlmesh-cache"} if IN_CONTAINER else {}),
    gateways={
        "postgres": GatewayConfig(
            connection=PostgresConnectionConfig(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
            )
        ),
        # DuckDB as the compute engine, PostgreSQL as the storage: the same database is
        # attached as DuckDB's only catalog, so a model here reads and writes PostgreSQL
        # tables like any other.
        #
        # It buys DuckDB's *grammar* -- ASOF JOIN, QUALIFY, PIVOT -- which pg_duckdb
        # cannot offer, PostgreSQL parsing the statement long before DuckDB sees it. It
        # costs a second engine and a round trip per row, so it is for a query the
        # grammar makes simpler or faster, not a default. Worked example:
        # models/silver/project_b/issue_risk_history.sql.
        "duckdb": GatewayConfig(
            connection=DuckDBConnectionConfig(
                catalogs={
                    database: DuckDBAttachOptions(
                        type="postgres",
                        path=attach_path(
                            dbname=database,
                            host=host,
                            port=port,
                            user=user,
                            password=password,
                        ),
                    )
                },
                extensions=["postgres", *duckdb_extensions],
            )
        ),
    },
    default_gateway="postgres",
    # A developer's machine defaults to "dev", so the command easiest to type is the
    # safe one and production takes a deliberate `sqlmesh plan prod`.
    default_target_environment="prod" if IN_CONTAINER else "dev",
    model_defaults=ModelDefaultsConfig(
        dialect="postgres",
        start="2026-06-10",  # Start date for backfill history
        cron="@daily",  # Daily at 12am UTC; a model may override it
    ),
    # https://sqlmesh.readthedocs.io/en/stable/guides/linter/
    linter=LinterConfig(
        enabled=True,
        rules=[
            "ambiguousorinvalidcolumn",
            "invalidselectstarexpansion",
            "noambiguousprojections",
        ],
    ),
)
