#!/bin/sh
# Build and run the whole stack locally with rootless podman, in development mode.
#
#   ./dev.sh              rebuild changed images and (re)start the stack
#   ./dev.sh down         stop and remove the pod (volumes and secrets are kept)
#   ./dev.sh logs         follow the combined logs of all containers
#   ./dev.sh serverstats  take one server-statistics sample now
#
# Run on a stack that is already up, `./dev.sh` refreshes it: podman's layer cache
# rebuilds only the images whose inputs changed, then the pod is recreated. It also starts
# a background loop sampling server statistics every SERVER_STATS_INTERVAL seconds,
# standing in for the deployment's systemd timer; `./dev.sh down` stops it.
#
# The local counterpart to build.sh + install.sh, which produce a release and deploy it as
# systemd quadlets. This builds straight into podman and runs one pod, so it works on a
# plain WSL Ubuntu without user-systemd. Always DEBUG: plain HTTP on 8080, no certificate.
# The wiring is kept identical to quadlets/ so dev and production behave the same.
set -e

cd "$(dirname "$0")"

# podman builds and runs everything here. Checked up front, as install.sh and run-tests.sh
# do, so a missing install says so rather than failing further down the script.
if ! command -v podman >/dev/null 2>&1; then
  echo "podman is not installed; it is required to build and run the dev stack." >&2
  exit 1
fi

# The ports the local stack publishes, captured before runtime.env is sourced over them.
# That file carries the deployment's ports, which are not what a developer wants -- its 80
# is one rootless podman cannot bind -- and sourcing under `set -a` would also overwrite a
# value given on the command line, e.g. `HTTP_PORT=9000 ./dev.sh up`. Empty here means
# "not asked for", so the dev defaults below apply.
ARG_HTTP_PORT="${HTTP_PORT:-}"
ARG_PG_PORT="${PG_PORT:-}"
ARG_SFTP_PORT="${SFTP_PORT:-}"
ARG_FLIGHT_PORT="${FLIGHT_PORT:-}"

# Build-time settings (image names, app name, paths) and the runtime ones (SERVER_NAME).
# DEBUG is forced on below, whatever runtime.env says.
set -a
. ./buildtime.env
. ./runtime.env
set +a

# SERVICES, render_build_templates and build_image.
. ./build-lib.sh

POD="${APP_NAME}"
# Plain HTTP for local development; mapped to the proxy's port 80 inside the pod. 8080
# avoids needing privileged ports, which rootless podman does not grant by default.
HTTP_PORT="${ARG_HTTP_PORT:-8080}"
# Publish PostgreSQL on the host too, so a local psql or DB GUI can connect. The
# in-pod port stays 5432; only the host port is exposed.
PG_PORT="${ARG_PG_PORT:-5432}"
# The dropzones SFTP endpoint. Published on the port it listens on, as the deployment does,
# so the address the admin panel shows uploaders is the one that answers.
SFTP_PORT="${ARG_SFTP_PORT:-2222}"
# The dropzones Arrow Flight endpoint, likewise.
FLIGHT_PORT="${ARG_FLIGHT_PORT:-8815}"
PG_USER="${SUPERUSER_NAME}"
PG_DB="${PG_DATABASE}"
# Publish on the IPv4 loopback explicitly. On WSL "localhost" often resolves to ::1
# first, which podman's pasta networking does not bind, so binding 127.0.0.1 keeps the
# printed URLs reachable. Use this address in the summary for the same reason.
HOST_ADDR="127.0.0.1"

# The sampling cadence for the background loop below, taken from buildtime.env, which is
# where the deployment's systemd timer gets it from too, so dev samples at the same rate.
SERVER_STATS_INTERVAL="${SERVER_STATS_INTERVAL:-60}"
# Where the loop's PID is recorded so a later run or `down` can stop it. Under the same
# state dir the collector already uses, so dev leaves nothing outside XDG paths.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/${APP_NAME}"
STATS_PIDFILE="$STATE_DIR/dev-serverstats.pid"

# Run the collector once against the dev stack. The deployment runs this on a systemd
# timer; locally there is no user-systemd, so it is invoked directly. POSTGRES_USER lets it
# authenticate as the dev superuser.
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

# Start the background loop that samples every SERVER_STATS_INTERVAL seconds, standing in
# for the systemd timer. Any earlier loop is stopped first so a re-run never leaves two.
# A collector run against a not-yet-ready or stopped stack fails; the loop ignores that and
# retries, so it is harmless until `down` kills it. Errors go to the dev log so a failing
# sample is visible without spamming stdout.
start_stats_loop() {
  mkdir -p "$STATE_DIR"
  stop_stats_loop
  (
    # Wait for Postgres to report healthy before the first sample, so data appears as soon
    # as the database is up rather than after a full interval. Bounded so the loop still
    # starts sampling (and logging its failures) if the container never turns healthy.
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
    # Follow every container's log at once; podman prefixes each line with the name.
    exec podman pod logs -f "$POD"
    ;;
  serverstats)
    # Take one server-statistics sample against the running dev stack, now.
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
# Same Dockerfiles and build settings as build.sh, but built with podman so the images land
# directly in the local rootless store the containers run from. Build output is quiet so a
# cached run does not look like real work; a failing build is re-run below with its log
# shown.
echo "Building images ..."

render_build_templates

for svc in $SERVICES; do
  printf '  %-11s ' "$svc"
  # Keep the happy path quiet; on failure re-run the same build so its full log is shown,
  # then abort (set -e alone would swallow the log we redirected away).
  if build_image podman "$svc" >/dev/null 2>&1; then
    echo "ok"
  else
    echo "FAILED"
    build_image podman "$svc"
    exit 1
  fi
done

# --- secrets --------------------------------------------------------------------------
# The machine credentials, generated like install.sh does. Created only if missing so
# they stay stable across runs (rotating crudman_password, for instance, would lock the
# app out of the existing database).
create_secret() {  # name, value
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}
create_secret "$SECRET_DJANGO_KEY"       "$(openssl rand -hex 32)"
create_secret "$SECRET_CRUDMAN_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_SQLMESH_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_GRAFANA_PASSWORD" "$(openssl rand -hex 32)"
# A placeholder for single sign-on, which a development system leaves off. It still has to
# exist: the crudman and grafana quadlets name it in a Secret=.
create_secret "$SECRET_OIDC_CLIENT" "unconfigured"

# The superuser login is a fixed, well-known value (SUPERUSER_DEFAULT_PASSWORD from
# buildtime.env) so the stack comes up unattended and the printed credentials are always
# correct. Unlike the secrets above it is set on every run (replacing any earlier value,
# e.g. one a previous install prompted for), because the crudman entrypoint resets the
# superuser password to this secret on start. This is a dev-only convenience and not for
# production.
podman secret rm "$SECRET_SUPERUSER_PASSWORD" >/dev/null 2>&1 || true
printf '%s' "$SUPERUSER_DEFAULT_PASSWORD" | podman secret create "$SECRET_SUPERUSER_PASSWORD" - >/dev/null

# --- volumes --------------------------------------------------------------------------
# Created up front so the rootless user owns their contents from the start (same reason as
# install.sh), one per service matching the *.volume quadlets.
for vol in postgresql_data grafana_data sftp_data proxy_data uploads_data; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done

# --- (re)create the pod ---------------------------------------------------------------
# A fresh pod each run keeps things reproducible; the data lives in the volumes, not the
# containers, so this loses nothing. The ports are published at the pod level, exactly as
# main.pod does.
podman pod rm -f "$POD" >/dev/null 2>&1 || true
podman pod create --name "$POD" \
  --publish "${HOST_ADDR}:${HTTP_PORT}:80" \
  --publish "${HOST_ADDR}:${PG_PORT}:5432" \
  --publish "${HOST_ADDR}:${SFTP_PORT}:${SFTP_PORT}" \
  --publish "${HOST_ADDR}:${FLIGHT_PORT}:${FLIGHT_PORT}" >/dev/null

# --- run the containers ---------------------------------------------------------------
# The wiring -- images, environment, secrets, volumes, healthchecks -- is read out of the
# quadlets in quadlets/ (see run_quadlet in build-lib.sh) rather than repeated here, so dev
# and the deployment cannot drift apart. Only what a development stack genuinely does
# differently is passed as an override below: DEBUG, and the addresses that name a
# published port, which no container can work out for itself.
#
# The containers share the pod's network namespace, so they reach each other on localhost
# just as the quadlet deployment does.

run_quadlet postgresql

run_quadlet crudman \
  -e "SERVER_NAME=${SERVER_NAME}" \
  -e DEBUG=true \
  -e "CSRF_TRUSTED_ORIGINS=http://${HOST_ADDR}:${HTTP_PORT}" \
  -e "SFTP_PORT=${SFTP_PORT}" -e "FLIGHT_PORT=${FLIGHT_PORT}"

# The dropzones SFTP and Arrow Flight endpoints: the crudman image in its "sftp" and
# "flight" roles, which the quadlets' Exec= lines select.
run_quadlet sftp -e "SERVER_NAME=${SERVER_NAME}" -e DEBUG=true -e "SFTP_PORT=${SFTP_PORT}"

run_quadlet flight -e "SERVER_NAME=${SERVER_NAME}" -e DEBUG=true -e "FLIGHT_PORT=${FLIGHT_PORT}"

run_quadlet sqlmesh

# The public address is spelled out rather than derived by the entrypoint, because no
# container can see the port it is published on. Same line the README gives custom ports.
run_quadlet grafana \
  -e "SERVER_NAME=${SERVER_NAME}" \
  -e "GF_SERVER_ROOT_URL=http://${HOST_ADDR}:${HTTP_PORT}/${GRAFANA_PATH}/"

# DEBUG=true makes the proxy entrypoint pick the plain-HTTP template, so the certificate
# directory the quadlet mounts is unnecessary; it is created empty so the mount resolves.
mkdir -p "$(quadlet_expand "$CERTIFICATE_PATH" | sed "s|%h|${HOME}|g")"
run_quadlet proxy -e DEBUG=true -e "SERVER_NAME=${SERVER_NAME}"

# --- background server-statistics sampling --------------------------------------------
# Replace the systemd timer the deployment uses with a background loop, so server_stats
# fills automatically in dev too. Started here, after the containers are up, so the first
# sample has a database to write to; stopped by `./dev.sh down`.
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

