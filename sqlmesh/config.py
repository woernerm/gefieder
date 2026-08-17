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

import os
from pathlib import Path

from dotenv import dotenv_values
from sqlmesh.core.config import (
    Config,
    GatewayConfig,
    ModelDefaultsConfig,
    PostgresConnectionConfig,
)
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
    # picks up either an exported variable or the gitignored file. The password is a
    # podman secret and deliberately lives in neither of the committed env files.
    password = os.environ.get("SQLMESH_PASSWORD")
    if not password:
        raise ValueError(
            "SQLMESH_PASSWORD is not set. Export it or write it to sqlmesh/.env. "
            "Read it on the server with: podman secret inspect --showsecret sqlmesh_password"
        )

config = Config(
    gateways={
        "postgres": GatewayConfig(
            connection=PostgresConnectionConfig(
                host=host,
                port=port,
                database=database,
                user="sqlmesh",
                password=password,
            )
        )
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
