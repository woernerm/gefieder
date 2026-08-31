#!/bin/sh
# Build and run the whole stack locally with rootless podman, in development mode.
#
#   ./dev.sh              rebuild changed images and (re)start the stack
#   ./dev.sh down         stop and remove the pod (volumes and secrets are kept)
#   ./dev.sh logs         follow the combined logs of all containers
#   ./dev.sh serverstats  take one server-statistics sample now
#
# On a stack that is already up, `./dev.sh` refreshes it: only the images whose inputs
# changed are rebuilt, then the pod is recreated. It also starts a background loop sampling
# server statistics, standing in for the deployment's systemd timer.
#
# The local counterpart to build.sh + install.sh. It builds straight into podman and runs
# one pod, so it works on a plain WSL Ubuntu without user-systemd. Always DEBUG: plain HTTP
# on 8080, no certificate. The wiring is read from quadlets/ so dev cannot drift.
set -e

cd "$(dirname "$0")"

# Checked up front, as install.sh and run-tests.sh do.
if ! command -v podman >/dev/null 2>&1; then
  echo "podman is not installed; it is required to build and run the dev stack." >&2
  exit 1
fi

# Captured before runtime.env is sourced over them: that file carries the deployment's
# ports, whose 80 rootless podman cannot bind, and `set -a` would also overwrite a value
# given on the command line. Empty means "not asked for", so the dev defaults apply.
ARG_HTTP_PORT="${HTTP_PORT:-}"
ARG_PG_PORT="${PG_PORT:-}"
ARG_SFTP_PORT="${SFTP_PORT:-}"
ARG_FLIGHT_PORT="${FLIGHT_PORT:-}"

# The build-time settings and the runtime ones. DEBUG is forced on below.
set -a
. ./buildtime.env
. ./runtime.env
set +a

# SERVICES, render_build_templates and build_image.
. ./build-lib.sh

POD="${APP_NAME}"
# Mapped to the proxy's port 80 inside the pod. 8080, because rootless podman does not
# grant privileged ports by default.
HTTP_PORT="${ARG_HTTP_PORT:-8080}"
# So a local psql or database GUI can connect; the in-pod port stays 5432.
PG_PORT="${ARG_PG_PORT:-5432}"
# Published on the port it listens on, as the deployment does, so the address the admin
# panel shows uploaders is the one that answers.
SFTP_PORT="${ARG_SFTP_PORT:-2222}"
# The dropzones Arrow Flight endpoint, likewise.
FLIGHT_PORT="${ARG_FLIGHT_PORT:-8815}"
PG_USER="${SUPERUSER_NAME}"
PG_DB="${PG_DATABASE}"
# On WSL "localhost" often resolves to ::1 first, which podman's pasta networking does not
# bind, so the IPv4 loopback is named explicitly here and in the summary.
HOST_ADDR="127.0.0.1"

# From buildtime.env, where the deployment's systemd timer gets it too.
SERVER_STATS_INTERVAL="${SERVER_STATS_INTERVAL:-60}"
# The loop's PID, so a later run or `down` can stop it. In the state dir the collector
# already uses, so dev leaves nothing outside XDG paths.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/${APP_NAME}"
STATS_PIDFILE="$STATE_DIR/dev-serverstats.pid"

# One collector run against the dev stack. The deployment uses a systemd timer; there is
# no user-systemd here, so it is invoked directly.
run_collector_once() {
  POSTGRES_USER="$PG_USER" POSTGRES_DB="$PG_DB" \
    SERVER_STATS_SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}" \
    ./serverstats/collect.sh
}

# Stop a previously started background collector loop, if one is running.
stop_stats_loop() {
  [ -f "$STATS_PIDFILE" ] || return 0
  pid="$(cat "$STATS_PIDFILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  rm -f "$STATS_PIDFILE"
}

# Stands in for the systemd timer. Any earlier loop is stopped first, so a re-run never
# leaves two. A run against a stopped stack fails, which the loop ignores and retries;
# errors go to the dev log rather than stdout.
start_stats_loop() {
  mkdir -p "$STATE_DIR"
  stop_stats_loop
  (
    # So data appears as soon as the database is up rather than after a full interval.
    # Bounded, so the loop still starts if the container never turns healthy.
    i=0
    while [ "$(podman inspect postgresql --format '{{.State.Health.Status}}' 2>/dev/null)" != "healthy" ] \
          && [ "$i" -lt 30 ]; do
      i=$((i + 1)); sleep 2
    done
    while true; do
      run_collector_once >>"$STATE_DIR/dev-serverstats.log" 2>&1 || true
      sleep "$SERVER_STATS_INTERVAL"
    done
  ) &
  echo "$!" > "$STATS_PIDFILE"
}

# --- subcommands ----------------------------------------------------------------------
case "${1:-up}" in
  down)
    stop_stats_loop
    podman pod rm -f "$POD" >/dev/null 2>&1 || true
    echo "Stopped and removed pod '$POD'. Volumes and secrets are kept."
    exit 0
    ;;
  logs)
    # podman prefixes each line with the container name.
    exec podman pod logs -f "$POD"
    ;;
  serverstats)
    # One sample against the running dev stack, now.
    run_collector_once
    exit $?
    ;;
  up) ;;
  *)
    echo "usage: $0 [up|down|logs|serverstats]" >&2
    exit 1
    ;;
esac

# --- build the images -----------------------------------------------------------------
# The same Dockerfiles and settings as build.sh, but with podman, so the images land in the
# local rootless store the containers run from. Quiet, so a cached run does not look like
# real work; a failing build is re-run below with its log shown.
echo "Building images ..."

render_build_templates

for svc in $SERVICES; do
  printf '  %-11s ' "$svc"
  # On failure re-run the same build so its full log is shown, then abort: set -e alone
  # would swallow the log redirected away.
  if build_image podman "$svc" >/dev/null 2>&1; then
    echo "ok"
  else
    echo "FAILED"
    build_image podman "$svc"
    exit 1
  fi
done

# --- secrets --------------------------------------------------------------------------
# Generated as install.sh does, and only if missing: rotating crudman_password would lock
# the app out of the existing database.
create_secret() {  # name, value
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}
create_secret "$SECRET_DJANGO_KEY"       "$(openssl rand -hex 32)"
create_secret "$SECRET_CRUDMAN_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_SQLMESH_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_GRAFANA_PASSWORD" "$(openssl rand -hex 32)"
# A placeholder for the single sign-on a development system leaves off. It still has to
# exist: the crudman and grafana quadlets name it in a Secret=.
create_secret "$SECRET_OIDC_CLIENT" "unconfigured"

# SUPERUSER_DEFAULT_PASSWORD from buildtime.env, so the stack comes up unattended and the
# printed credentials are always right. Set on every run, replacing whatever a previous
# install prompted for, because the crudman entrypoint resets the password to it at start.
# A dev-only convenience.
podman secret rm "$SECRET_SUPERUSER_PASSWORD" >/dev/null 2>&1 || true
printf '%s' "$SUPERUSER_DEFAULT_PASSWORD" | podman secret create "$SECRET_SUPERUSER_PASSWORD" - >/dev/null

# --- volumes --------------------------------------------------------------------------
# Created up front so the rootless user owns their contents, as install.sh does.
for vol in postgresql_data grafana_data sftp_data proxy_data uploads_data; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done

# --- (re)create the pod ---------------------------------------------------------------
# A fresh pod each run, the data living in the volumes rather than the containers. The
# ports are published at the pod level, as main.pod does.
podman pod rm -f "$POD" >/dev/null 2>&1 || true
podman pod create --name "$POD" \
  --publish "${HOST_ADDR}:${HTTP_PORT}:80" \
  --publish "${HOST_ADDR}:${PG_PORT}:5432" \
  --publish "${HOST_ADDR}:${SFTP_PORT}:${SFTP_PORT}" \
  --publish "${HOST_ADDR}:${FLIGHT_PORT}:${FLIGHT_PORT}" >/dev/null

# --- run the containers ---------------------------------------------------------------
# The wiring -- images, environment, secrets, volumes, healthchecks -- is read out of
# quadlets/ (run_quadlet in build-lib.sh) rather than repeated here. Only what a development
# stack genuinely does differently is overridden below: DEBUG, and the addresses naming a
# published port, which no container can work out for itself.
#
# The containers share the pod's network namespace, so they reach each other on localhost
# as the quadlet deployment does.

run_quadlet postgresql

run_quadlet crudman \
  -e "SERVER_NAME=${SERVER_NAME}" \
  -e DEBUG=true \
  -e "CSRF_TRUSTED_ORIGINS=http://${HOST_ADDR}:${HTTP_PORT}" \
  -e "SFTP_PORT=${SFTP_PORT}" -e "FLIGHT_PORT=${FLIGHT_PORT}"

# The crudman image in its "sftp" and "flight" roles, which the quadlets' Exec= lines
# select.
run_quadlet sftp -e "SERVER_NAME=${SERVER_NAME}" -e DEBUG=true -e "SFTP_PORT=${SFTP_PORT}"

run_quadlet flight -e "SERVER_NAME=${SERVER_NAME}" -e DEBUG=true -e "FLIGHT_PORT=${FLIGHT_PORT}"

run_quadlet sqlmesh

# Spelled out rather than derived, no container seeing the port it is published on. The
# same line the README gives custom ports.
run_quadlet grafana \
  -e "SERVER_NAME=${SERVER_NAME}" \
  -e "GF_SERVER_ROOT_URL=http://${HOST_ADDR}:${HTTP_PORT}/${GRAFANA_PATH}/"

# DEBUG=true picks the plain-HTTP template, so the certificate directory the quadlet
# mounts is only created for the mount to resolve.
mkdir -p "$(quadlet_expand "$CERTIFICATE_PATH" | sed "s|%h|${HOME}|g")"
run_quadlet proxy -e DEBUG=true -e "SERVER_NAME=${SERVER_NAME}"

# --- background server-statistics sampling --------------------------------------------
# A background loop in place of the deployment's systemd timer. Started after the
# containers are up, so the first sample has a database to write to.
start_stats_loop

# --- summary --------------------------------------------------------------------------
cat <<EOF

${APP_NAME} is starting in development mode (plain HTTP, no certificate).

  Admin panel:  http://${HOST_ADDR}:${HTTP_PORT}/${CRUDMAN_PATH}/
  Model docs:   http://${HOST_ADDR}:${HTTP_PORT}/${CRUDMAN_PATH}/docs/
  Grafana:      http://${HOST_ADDR}:${HTTP_PORT}/${GRAFANA_PATH}/
  Login:        ${SUPERUSER_NAME} / ${SUPERUSER_DEFAULT_PASSWORD}

  PostgreSQL:   host=${HOST_ADDR} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER}
                (password = the superuser password, same as the admin login)
                psql "host=${HOST_ADDR} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER}"

  SFTP:         port ${SFTP_PORT} for dropzones with the SFTP method
                (each dropzone's admin page shows its address; the secret is the password)

  Arrow Flight: port ${FLIGHT_PORT} for dropzones with the Arrow Flight method
                (each dropzone's admin page shows its address and a ready-to-run client)

  Follow logs:  ./dev.sh logs
  Stop:         ./dev.sh down

Server statistics are sampled every ${SERVER_STATS_INTERVAL}s in the background (the
server_stats schema and the Grafana monitoring dashboard fill on their own). Run one sample
now with ./dev.sh serverstats.

The database needs a few seconds to initialise on the first run.
EOF

