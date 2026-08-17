#!/bin/sh
set -e

# This script and the engine log to stdout/stderr only; journald captures the stream and
# is what survives a crash, a container replacement and a restart. It rotates and
# size-caps the log on its own, which a file on the volume did not.
#
# Every sqlmesh invocation below therefore passes --log-to-stdout, which adds the stdout
# handler its own logger otherwise lacks: without it the engine's INFO/ERROR records (the
# detail behind a failed plan or run) go only to its log files and never reach journald.
# SQLMesh always writes those files as well -- the file handler cannot be turned off, only
# pointed elsewhere -- so --log-file-dir sends them to a tmpfs-like path under /tmp that is
# discarded with the container, rather than accumulating in the image's project directory.
#
# These lines carry SQLMesh's own timestamp in addition to journald's, which is the one
# place the "a single timestamp per message" rule cannot be met: SQLMesh formats its
# records with a hardcoded module constant that no setting or environment variable
# overrides. The container therefore runs on the host's timezone (Timezone=local in
# sqlmesh.container) so that the second stamp at least agrees with journald's instead of
# reading hours apart from it.
SQLMESH_LOG_ARGS="--log-to-stdout --log-file-dir /tmp/sqlmesh-logs"

# Expose the database password from the mounted secret for the connection check below.
# config.py reads the secret file itself, so this is only for the psycopg2 call.
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

# Apply the current state of the project before starting the scheduling loop. The log
# options are global to the sqlmesh command group, so they precede the subcommand.
uv run --project /sqlmesh sqlmesh $SQLMESH_LOG_ARGS plan --auto-apply --no-prompts

# Exit promptly on SIGTERM/SIGINT so "podman stop" does not have to resort to
# SIGKILL. The shell only handles signals once the current foreground command has
# finished, so the sleep runs in the background and is awaited instead: "wait" is
# interruptible and lets the trap fire immediately.
trap 'exit 0' TERM INT

# Execute the models that are due according to their cron schedules. A failed run
# only logs an error so that a transient database outage does not kill the loop.
while true; do
  uv run --project /sqlmesh sqlmesh $SQLMESH_LOG_ARGS run \
    || echo "sqlmesh run failed, retrying after the next interval"
  sleep "${SQLMESH_RUN_INTERVAL:-10}" &
  wait $!
done
