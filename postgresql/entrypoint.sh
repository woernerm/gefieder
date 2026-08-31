#!/usr/bin/env bash
# Re-apply the database's structural init scripts on every start, then hand over to the
# base image's entrypoint.
#
# The base image runs /docker-entrypoint-initdb.d/ only when the data directory is empty,
# making those scripts a first-install-only step: a schema, grant or function added later
# never reaches a deployment that already has a volume, and anything dropped by hand stays
# dropped. That is what let the crudman schema go missing and surface as "permission denied
# for schema public", PostgreSQL falling through a search_path entry naming no schema.
#
# So the structural scripts are idempotent and run again here against the live database,
# which is what repairs a missing schema and carries a new grant onto an existing
# deployment.
#
# Two scripts are deliberately excluded, marked by "once" in their filename:
#   gf_0001_once_configure_settings.sh    appends to postgresql.conf; re-running it would
#                                         duplicate every line on each boot.
#   gf_0006_once_create_example_tenants.sql  seeds example tenants; an administrator is
#                                         meant to delete them, and a re-run would
#                                         resurrect them on the next restart.
set -e

INITDB_DIR=/docker-entrypoint-initdb.d

# Only for the server itself: the base entrypoint re-executes this script for its own
# purposes, when the server is not up yet.
if [ "$1" != postgres ]; then
  exec /usr/local/bin/docker-entrypoint.sh "$@"
fi

# On an empty data directory the base entrypoint runs initdb and executes every script
# itself, "once" ones included, so the first install is left entirely to it.
if [ -s "$PGDATA/PG_VERSION" ]; then
  # The scripts need a running server, and the base entrypoint starts PostgreSQL only
  # after this process execs into it. So a temporary one is bound to the unix socket
  # alone, unreachable from outside the container.
  #
  # It must not be left running, or the base entrypoint finds the same PGDATA locked. The
  # EXIT trap also covers the "set -e" exit a failing script triggers.
  stop_temporary_server() {
    pg_ctl -D "$PGDATA" -m fast -w stop >/dev/null 2>&1 || true
  }
  trap 'stop_temporary_server' EXIT

  # This shell is PID 1 and neither acts on INT/TERM by default nor passes them to the
  # temporary server pg_ctl started as a child, so an unhandled "podman stop" waits out
  # its grace period and SIGKILLs the container mid-script.
  #
  # A trap runs only once the foreground command returns, so the handler cannot interrupt
  # a running psql; it records the request and the loop checks it between scripts. Each is
  # short, and stopping here is safe: nothing has been served, and the next start
  # re-applies them.
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
