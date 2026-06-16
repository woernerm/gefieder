#!/bin/sh
set -e

# Expose the database password from the mounted secret to the env_var() templating
# in config.yaml.
SQLMESH_PASSWORD="$(cat /run/secrets/sqlmesh_password)"
export SQLMESH_PASSWORD

# Wait until PostgreSQL accepts connections, because the containers in the pod start
# without ordering and this one can come up while the database is still initializing.
# The connection is tested with psycopg2 directly because "sqlmesh info" exits with
# code 0 even when the data warehouse connection fails.
until uv run --project /sqlmesh python -c "
import os, psycopg2
psycopg2.connect(
    host=os.environ.get('POSTGRES_HOST', 'localhost'),
    port=os.environ.get('POSTGRES_PORT', '5432'),
    dbname=os.environ.get('POSTGRES_DB', 'postgres'),
    user='sqlmesh',
    password=os.environ['SQLMESH_PASSWORD'],
).close()
" >/dev/null 2>&1; do
  echo "Waiting for the database to become available..."
  sleep 2
done

# Apply the current state of the project before starting the scheduling loop.
uv run --project /sqlmesh sqlmesh plan --auto-apply --no-prompts

# Exit promptly on SIGTERM/SIGINT so "podman stop" does not have to resort to
# SIGKILL. The shell only handles signals once the current foreground command has
# finished, so the sleep runs in the background and is awaited instead: "wait" is
# interruptible and lets the trap fire immediately.
trap 'exit 0' TERM INT

# Execute the models that are due according to their cron schedules. A failed run
# only logs an error so that a transient database outage does not kill the loop.
while true; do
  uv run --project /sqlmesh sqlmesh run || echo "sqlmesh run failed, retrying after the next interval"
  sleep "${SQLMESH_RUN_INTERVAL:-10}" &
  wait $!
done
