# SQLMesh project configuration.
#
# Python rather than YAML, because the same project is loaded from two places with
# different connection settings, and a Python config is evaluated when SQLMesh reads it:
# it can work out for itself where it is running instead of relying on the caller to pass
# a flag. `sqlmesh plan` therefore does the right thing both in the container and on a
# developer's machine, with nothing to remember and no file to edit between the two.
#
# YAML cannot do this. It renders `env_var()` placeholders but holds a single config, so
# selecting between two of them needs `--config`, which the YAML loader rejects outright.
#
# SQLMesh refuses to start if a config.yaml sits next to this file: it loads at most one
# config per directory. Do not reintroduce one.

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

# The podman secret is mounted only in the container, which makes its presence the
# honest answer to "am I the deployed engine or a developer's checkout?" -- unlike a
# hostname or an environment variable, it cannot accidentally be true in the wrong place.
SECRET_PATH = Path("/run/secrets/sqlmesh_password")
IN_CONTAINER = SECRET_PATH.exists()

if IN_CONTAINER:
    # The quadlet (sqlmesh.container) sets these; the pod shares one network namespace,
    # so the database is on localhost. The password is read from the secret rather than
    # from the environment so that `podman exec sqlmesh sqlmesh ...` works too -- that
    # shell never sees the export entrypoint.sh does for its own connection check.
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ.get("POSTGRES_DB", "postgres")
    password = SECRET_PATH.read_text().strip()
    # The deployed engine owns production, so it keeps the shared service role.
    user = "sqlmesh"
else:
    # On a developer's machine the database is reached over the network, on the port the
    # pod publishes. SERVER_NAME is the address the system is reached under, so it is
    # already the right one -- and it names a local development stack just as well as a
    # server, which is why no separate development setting is needed here.
    runtime_env = dotenv_values(Path(__file__).resolve().parent.parent / "runtime.env")
    host = runtime_env["SERVER_NAME"]
    port = 5432
    database = "postgres"
    # SQLMesh loads sqlmesh/.env into the environment before importing this file, so this
    # picks up either an exported variable or the gitignored file.
    password = os.environ.get("SQLMESH_PASSWORD")
    if not password:
        raise ValueError(
            "SQLMESH_PASSWORD is not set. Export it or write it to sqlmesh/.env. "
            "It is the password of your own database account, issued once when an "
            "administrator provisioned it in crudman under Database access."
        )

    # Developers connect as themselves, not as the deployed engine. The shared sqlmesh
    # secret therefore never leaves the server: it belongs to the container and to CI,
    # which is what makes a query on this database traceable to a person and a departure
    # a matter of disabling one role.
    #
    # The role name is derived exactly as crudman derives it when provisioning (see
    # dbusers.utils.role_name_for), so nothing has to be looked up or configured. Someone
    # whose local account is named differently from their login here overrides it with
    # SQLMESH_USER.
    user = os.environ.get("SQLMESH_USER") or (
        "gf_u_" + re.sub(r"[^a-z0-9]+", "_", getpass.getuser().strip().lower()).strip("_")
    )[:50]

def attach_path(**settings: object) -> str:
    """Build the libpq connection string DuckDB attaches PostgreSQL with.

    Two layers of quoting have to survive each other. Each value is single-quoted for
    libpq, because a password may hold a space; the whole string is then escaped for the
    literal SQLMesh wraps it in (`ATTACH '<path>'`), where DuckDB reads a doubled quote as
    one. Skipping either layer works right up until a password is not a tame hex string.
    """

    def quoted(value: object) -> str:
        escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    conninfo = " ".join(f"{key}={quoted(value)}" for key, value in settings.items())
    return conninfo.replace("'", "''")


# The extensions the image installed (see sqlmesh/Dockerfile), read back rather than listed
# again so the gateway loads exactly what is on disk. Empty on a developer's machine, where
# DuckDB downloads what a model asks for instead.
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
        # DuckDB as the compute engine, PostgreSQL as the storage. It attaches the same
        # database over the same connection and, being the only catalog, makes it DuckDB's
        # default -- so a model that asks for this gateway reads the bronze tables and
        # writes its own table in PostgreSQL like any other. Grafana, the silver union and
        # gold never learn which engine built it, and state stays in PostgreSQL because
        # the default gateway keeps it.
        #
        # What it buys is DuckDB's *grammar*: ASOF JOIN, QUALIFY, PIVOT. pg_duckdb cannot
        # offer those however hard it accelerates the execution, because PostgreSQL parses
        # the statement long before DuckDB sees it. What it costs is a second engine and a
        # round trip over the wire for every row read and written, so it earns its place
        # for a query the grammar makes simpler or faster, not as a default.
        # models/silver/project_b/issue_risk_history.sql is the worked example.
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
    # Which virtual environment a bare `sqlmesh plan` or `sqlmesh run` targets. The
    # deployed engine maintains production; a developer's machine defaults to a "dev"
    # environment instead, so the command that is easiest to type is also the safe one
    # and reaching production takes a deliberate `sqlmesh plan prod`.
    default_target_environment="prod" if IN_CONTAINER else "dev",
    model_defaults=ModelDefaultsConfig(
        dialect="postgres",
        start="2026-06-10",  # Start date for backfill history
        cron="@daily",  # Run models daily at 12am UTC (can override per model)
    ),
    # Enforce standards for your team:
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
