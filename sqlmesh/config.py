"""SQLMesh project configuration.

Python rather than YAML, because the same project is loaded from two places with
different connection settings and a Python config is evaluated when SQLMesh reads it: it
works out for itself where it is running, so `sqlmesh plan` does the right thing both in
the container and on a developer's machine. YAML holds a single config, so selecting
between two needs `--config`, which its loader rejects outright.

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
variable its presence cannot accidentally be true in the wrong place.
"""

if IN_CONTAINER:
    # The quadlet sets these; the pod shares one network namespace, so the database is on
    # localhost. The password comes from the secret rather than the environment so that
    # `podman exec sqlmesh sqlmesh ...` works too — that shell never sees the export
    # entrypoint.sh does for its own connection check.
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ.get("POSTGRES_DB", "postgres")
    password = SECRET_PATH.read_text().strip()
    # The deployed engine owns production, so it keeps the shared service role, named by
    # SQLMESH_DB_USER in buildtime.env and passed in by sqlmesh.container.
    user = os.environ.get("POSTGRES_USER", "sqlmesh")
else:
    # On a developer's machine the database is reached over the network, on the port the
    # pod publishes. SERVER_NAME is already the right address, and it names a local
    # development stack as well as a server, so no separate setting is needed.
    repo_root = Path(__file__).resolve().parent.parent
    runtime_env = dotenv_values(repo_root / "runtime.env")
    host = runtime_env["SERVER_NAME"]
    port = 5432
    # A build-time setting, so it comes from the other file; the deployed engine gets the
    # same value from POSTGRES_DB above, which the quadlet fills from it.
    database = dotenv_values(repo_root / "buildtime.env")["PG_DATABASE"]
    # SQLMesh loads sqlmesh/.env into the environment before importing this file, so this
    # picks up either an exported variable or the gitignored file.
    password = os.environ.get("SQLMESH_PASSWORD")
    if not password:
        raise ValueError(
            "SQLMESH_PASSWORD is not set. Export it or write it to sqlmesh/.env. "
            "It is the password of your own database account, issued once when an "
            "administrator provisioned it in crudman under Database access."
        )

    # Developers connect as themselves, so the shared sqlmesh secret never leaves the
    # server: that is what makes a query traceable to a person and a departure a matter of
    # disabling one role.
    #
    # The name is derived exactly as crudman derives it when provisioning (see
    # dbusers.utils.role_name_for), and the prefix comes from the same buildtime.env the
    # database was initialised from, so nothing has to be looked up or configured. Someone
    # whose local account is named differently overrides the whole name with SQLMESH_USER.
    role_prefix = dotenv_values(repo_root / "buildtime.env").get("DB_USER_PREFIX", "gf_")
    user = os.environ.get("SQLMESH_USER") or (
        role_prefix
        + re.sub(r"[^a-z0-9]+", "_", getpass.getuser().strip().lower()).strip("_")
    )[:50]

def attach_path(**settings: object) -> str:
    """Build the libpq connection string DuckDB attaches PostgreSQL with.

    Two layers of quoting have to survive each other, and skipping either works right up
    until a password is not a tame hex string.

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


# Read back from the image rather than listed again, so the gateway loads exactly what is
# on disk. Empty on a developer's machine, where DuckDB downloads what a model asks for.
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
        # DuckDB as the compute engine, PostgreSQL as the storage: it attaches the same
        # database over the same connection and, being the only catalog, makes it
        # DuckDB's default, so a model on this gateway reads and writes PostgreSQL tables
        # like any other and nothing downstream learns which engine built it.
        #
        # What it buys is DuckDB's *grammar* — ASOF JOIN, QUALIFY, PIVOT — which pg_duckdb
        # cannot offer however hard it accelerates execution, PostgreSQL parsing the
        # statement long before DuckDB sees it. What it costs is a second engine and a
        # round trip per row, so it earns its place for a query the grammar makes simpler
        # or faster, not as a default. The worked example is
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
    # The deployed engine maintains production; a developer's machine defaults to "dev",
    # so the command easiest to type is the safe one and reaching production takes a
    # deliberate `sqlmesh plan prod`.
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
