#!/bin/sh
set -e

# Persist everything this script and the engine print into the mounted log volume while
# still echoing to stdout, so "podman logs"/journald keep working and a crash also leaves
# its cause on disk. Process substitution is a bashism unavailable in this dash /bin/sh,
# so on first entry the script re-runs itself with stdout+stderr piped through tee -a
# (which appends, so logs survive restarts); GEFIEDER_LOGGING guards against looping. A
# pipeline cannot be exec'd and its status would be tee's, so the real exit status is
# captured via a status file and re-raised, keeping the container's exit code (and thus
# Restart=) honest on a crash. The SIGTERM trap below still fires: it runs in the re-run
# child, which is the foreground process here. The volume is owned by the podman user.
LOG_DIR=/var/log/gefieder
if [ -z "$GEFIEDER_LOGGING" ]; then
  mkdir -p "$LOG_DIR"
  export GEFIEDER_LOGGING=1
  STATUS_FILE="$(mktemp)"
  # Prefix every line with an ISO-8601 timestamp before persisting it, so each line on
  # disk can be placed in time. This script's echoes and sqlmesh's plan/run output are
  # not timestamped on their own. A shell read loop is used rather than awk because mawk
  # (this image's awk) buffers its input in large blocks, so a slow stream like the run
  # loop would sit unwritten for a long time; `read` emits each line at once. The
  # `|| [ -n "$line" ]` flushes a final line that lacks a trailing newline.
  { "$0" "$@"; echo $? > "$STATUS_FILE"; } 2>&1 \
    | while IFS= read -r line || [ -n "$line" ]; do
        printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$line"
      done | tee -a "$LOG_DIR/sqlmesh.log" || true
  status="$(cat "$STATUS_FILE" 2>/dev/null || echo 1)"; rm -f "$STATUS_FILE"
  exit "$status"
fi

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
