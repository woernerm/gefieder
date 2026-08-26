#!/usr/bin/env bash
# Re-apply the database's structural init scripts on every start, then hand over to the
# base image's entrypoint.
#
# The base image runs /docker-entrypoint-initdb.d/ only when the data directory is empty,
# which makes those scripts a first-install-only step: a schema, grant or function added
# to them afterwards never reaches a deployment that already has a volume, and anything
# dropped by hand stays dropped. That gap is what let the crudman schema go missing and
# surface as "permission denied for schema public" -- PostgreSQL silently ignores a
# search_path entry naming no existing schema and falls through to the next one.
#
# So the structural scripts are written to be idempotent and run again here, against the
# live database, every time the container starts. Re-running them is what repairs a
# missing schema and what carries a newly added grant onto an existing deployment.
#
# Two scripts are deliberately excluded, marked by "once" in their filename:
#   gf_0001_once_configure_settings.sh    appends to postgresql.conf; re-running it would
#                                         duplicate every line on each boot.
#   gf_0006_once_create_example_tenants.sql  seeds example tenants; an administrator is
#                                         meant to delete them, and a re-run would
#                                         resurrect them on the next restart.
set -e

INITDB_DIR=/docker-entrypoint-initdb.d

# Only for the server itself. The base entrypoint re-executes this script for its own
# purposes (and runs other commands during initdb), and re-applying then would run against
# a server that is not up yet.
if [ "$1" != postgres ]; then
  exec /usr/local/bin/docker-entrypoint.sh "$@"
fi

# An empty data directory means the base entrypoint is about to run initdb and will
# execute every script itself, including the "once" ones. Doing it here as well would only
# apply them twice, so leave the first install entirely to it.
if [ -s "$PGDATA/PG_VERSION" ]; then
  # The scripts talk to a running server, which there is not one of yet: the base
  # entrypoint starts PostgreSQL only after this process execs into it. So start a
  # temporary server bound to the unix socket only -- nothing outside the container can
  # reach it while the structural scripts run.
  # Whatever happens below, the temporary server must not be left running: the base
  # entrypoint starts its own on the same PGDATA and would find the directory locked. The
  # EXIT trap also covers the "set -e" exit a failing script triggers.
  stop_temporary_server() {
    pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true
  }
  trap 'stop_temporary_server' EXIT

  # A stop arriving during this phase has to be answered, because this shell is PID 1 and
  # a shell neither acts on INT/TERM by default nor passes them to the temporary server,
  # which pg_ctl started as a child rather than in this process. Left unhandled, "podman
  # stop" (and the stop half of "podman restart") waits out its ten-second grace period
  # and SIGKILLs the container mid-script.
  #
  # A POSIX shell runs a trap only once the current foreground command returns, so the
  # handler cannot interrupt a running psql; it records the request and the loop below
  # checks it between scripts. Each script is short, so the wait stays well inside the
  # grace period. Stopping here is safe: nothing outside the container has been served
  # yet, and the next start simply applies the scripts again.
  stop_requested=
  trap 'stop_requested=yes' INT TERM

  echo "Re-applying the structural init scripts to the existing database ..."
  PGUSER="${PGUSER:-$POSTGRES_USER}" \
    pg_ctl -D "$PGDATA" -o "-c listen_addresses='' -p 5432" -w start

  for script in "$INITDB_DIR"/*; do
    if [ -n "$stop_requested" ]; then
      echo "Stop requested; leaving the remaining scripts to the next start."
      exit 143
    fi
    case "$script" in
      *once*) continue ;;
    esac
    echo "  $(basename "$script")"
    case "$script" in
      *.sh)  . "$script" ;;
      *.sql) psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
                  --dbname "$POSTGRES_DB" -f "$script" >/dev/null ;;
    esac
  done

  trap - EXIT INT TERM
  pg_ctl -D "$PGDATA" -m fast -w stop
  echo "Structural init scripts applied."
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
