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
from pathlib import Path

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

if IN_CONTAINER:
    # The quadlet sets these; the pod shares one network namespace, so the database is
    # on localhost. The password comes from the secret rather than the environment, so
    # `podman exec sqlmesh sqlmesh ...` works too.
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ.get("POSTGRES_DB", "postgres")
    password = SECRET_PATH.read_text().strip()
    # The deployed engine owns production, so it keeps the shared service role.
    user = os.environ.get("POSTGRES_USER", "sqlmesh")
else:
    # Over the network on a developer's machine, on the port the pod publishes.
    # SERVER_NAME names a local development stack as well as a server.
    repo_root = Path(__file__).resolve().parent.parent
    runtime_env = dotenv_values(repo_root / "runtime.env")
    host = runtime_env["SERVER_NAME"]
    port = 5432
    # A build-time setting, so it comes from the other file; the quadlet fills
    # POSTGRES_DB from the same value.
    database = dotenv_values(repo_root / "buildtime.env")["PG_DATABASE"]
    # SQLMesh loads sqlmesh/.env before importing this file, so an exported variable and
    # the gitignored file both work.
    password = os.environ.get("SQLMESH_PASSWORD")
    if not password:
        raise ValueError(
            "SQLMESH_PASSWORD is not set. Export it or write it to sqlmesh/.env. "
            "It is the password of your own database account, issued once when an "
            "administrator provisioned it in crudman under Database access."
        )

    # Developers connect as themselves, so the shared sqlmesh secret never leaves the
    # server, a query stays traceable to a person and a departure is one role disabled.
    # The name is derived as crudman derives it when provisioning (dbusers.utils.
    # role_name_for), from the buildtime.env the database was initialised from. Someone
    # whose local account is named differently overrides it with SQLMESH_USER.
    role_prefix = dotenv_values(repo_root / "buildtime.env").get("DB_USER_PREFIX", "gf_")
    user = os.environ.get("SQLMESH_USER") or (
        role_prefix
        + re.sub(r"[^a-z0-9]+", "_", getpass.getuser().strip().lower()).strip("_")
    )[:50]

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
