#!/bin/sh
set -e

# This script and the engine log to stdout/stderr only, so journald captures and rotates
# the stream, which a file on the volume did not.
#
# Every sqlmesh invocation passes --log-to-stdout, the stdout handler its logger otherwise
# lacks. SQLMesh writes its log files regardless -- the file handler can be pointed
# elsewhere but not turned off -- so --log-file-dir sends them under /tmp.
#
# These lines carry SQLMesh's own timestamp on top of journald's, the one place the single
# timestamp rule cannot be met, its format being a hardcoded module constant. The container
# runs on the host's timezone so the two at least agree.
SQLMESH_LOG_ARGS="--log-to-stdout --log-file-dir /tmp/sqlmesh-logs"

# Only for the psycopg2 call below; config.py reads the secret file itself.
SQLMESH_PASSWORD="$(cat "/run/secrets/${SECRET_SQLMESH_PASSWORD:-sqlmesh_password}")"
export SQLMESH_PASSWORD

# The containers in the pod start without ordering, so this one can come up while the
# database is still initializing. Tested with psycopg2 directly, "sqlmesh info" exiting 0
# even when the warehouse connection fails.
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

# Before the scheduling loop. The log options are global to the command group, so they
# precede the subcommand.
uv run --project /sqlmesh sqlmesh $SQLMESH_LOG_ARGS plan --auto-apply --no-prompts

# So "podman stop" need not resort to SIGKILL. A shell handles signals only once the
# foreground command finishes, so the sleep runs in the background and is awaited: "wait"
# is interruptible and lets the trap fire at once.
trap 'exit 0' TERM INT

# The models due by their cron schedules. A failed run only logs, so a transient database
# outage does not kill the loop.
while true; do
  uv run --project /sqlmesh sqlmesh $SQLMESH_LOG_ARGS run \
    || echo "sqlmesh run failed, retrying after the next interval"
  sleep "${SQLMESH_RUN_INTERVAL:-10}" &
  wait $!
done
