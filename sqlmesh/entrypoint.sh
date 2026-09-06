#!/bin/sh
set -e

# The engine runs the models crudman checked out into the shared volume, not a project
# baked into this image. So the loop below does two things: it applies a plan whenever the
# deployed commit changes, and it evaluates whatever is due on its cron in between.
#
# This script and the engine log to stdout/stderr only, so journald captures and rotates
# the stream, which a file on the volume did not.
#
# The engine is invoked through /usr/local/bin/sqlmesh, which settles where the project is
# and where the log files go; see that file. The same wrapper is what an operator reaches
# with "podman exec sqlmesh sqlmesh ...".
#
# Its lines carry SQLMesh's own timestamp on top of journald's, the one place the single
# timestamp rule cannot be met, its format being a hardcoded module constant. The container
# runs on the host's timezone so the two at least agree.

# For the tools beside the CLI, which are Python scripts in this image rather than commands.
PYTHON="uv run --project /sqlmesh python"

MODELS_DIR="${MODELS_DIR:-/var/lib/app/models}"
PROJECT="${MODELS_DIR}/deployed/sqlmesh"

# Written by crudman as the last step of a checkout, so it names the deployed commit *and*
# says the tree is complete. Read with cat rather than git, which this image does not have.
MARKER="${MODELS_DIR}/deployed.sha"

# Only for the psycopg2 call below; config.py reads the secret file itself.
SQLMESH_PASSWORD="$(cat "/run/secrets/${SECRET_SQLMESH_PASSWORD:-sqlmesh_password}")"
export SQLMESH_PASSWORD

# The containers in the pod start without ordering, so this one can come up while the
# database is still initializing. Tested with psycopg2 directly, "sqlmesh info" exiting 0
# even when the warehouse connection fails.
until $PYTHON -c "
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

# crudman clones the repository and checks a commit out on its own start. Ordering after it
# is only a hint, so the wait is here as well.
until [ -f "$MARKER" ] && [ -d "$PROJECT" ]; do
  echo "Waiting for crudman to check the models out..."
  sleep 2
done

# So "podman stop" need not resort to SIGKILL. A shell handles signals only once the
# foreground command finishes, so the sleep runs in the background and is awaited: "wait"
# is interruptible and lets the trap fire at once.
trap 'exit 0' TERM INT

# Applies the deployed commit and reports the outcome to the row crudman created for it.
# The log is kept rather than streamed alone, so the failure a person reads on the versions
# page is the one journald recorded.
apply() {  # commit sha
  echo "Deploying models ${1}"

  # Each step is announced before it starts, so the page names the one taking the time
  # rather than saying only that something is.
  $PYTHON /sqlmesh/status.py "$1" transforming
  if sqlmesh plan --auto-apply --no-prompts >/tmp/plan.log 2>&1; then
    cat /tmp/plan.log
    # The documentation of what now runs. Exported after the plan, so a model that failed
    # to build is not described as though it had. It loads the project a second time,
    # which is why it is a step of its own rather than part of the one before it.
    $PYTHON /sqlmesh/status.py "$1" documenting
    $PYTHON /sqlmesh/docs_export.py "$PROJECT" /tmp/docs.json >/dev/null
    $PYTHON /sqlmesh/status.py "$1" succeeded /tmp/docs.json
    return 0
  fi

  cat /tmp/plan.log >&2
  $PYTHON /sqlmesh/status.py "$1" failed </tmp/plan.log
  return 1
}

applied=""
live=""
while true; do
  deployed="$(cat "$MARKER" 2>/dev/null || true)"

  # A new commit is planned once. A failure is recorded and not retried: the same tree
  # would fail the same way, and the fix is another commit, which changes this value.
  if [ -n "$deployed" ] && [ "$deployed" != "$applied" ]; then
    applied="$deployed"
    if apply "$deployed"; then live="$deployed"; else live=""; fi
  fi

  # The models due by their cron schedules, but only once a plan has succeeded: there is
  # nothing to evaluate before that, and the failure is already reported. A failed run only
  # logs, so a transient database outage does not kill the loop.
  if [ -n "$live" ]; then
    sqlmesh run || echo "sqlmesh run failed, retrying after the next interval"
  fi

  sleep "${SQLMESH_RUN_INTERVAL:-10}" &
  wait $!
done
