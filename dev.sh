#!/bin/sh
# Build and run the whole stack locally with rootless podman, in development mode.
#
#   ./dev.sh            rebuild changed images and (re)start the stack
#   ./dev.sh down         stop and remove the pod (volumes and secrets are kept)
#
# Run on a stack that is already up, `./dev.sh` refreshes it: podman's layer cache rebuilds
# only the images whose inputs changed, then the pod is torn down and recreated from the
# current images. Nothing to stop first; an unchanged run is quick.
#   ./dev.sh logs         follow the combined logs of all containers
#   ./dev.sh serverstats  take one server-statistics sample now
#
# `./dev.sh` also starts a background loop that samples server statistics every
# SERVER_STATS_INTERVAL seconds, standing in for the systemd timer the deployment uses so
# the server_stats schema (and the Grafana monitoring dashboard) fills on its own. The loop
# is stopped by `./dev.sh down` and replaced on each `./dev.sh` run.
#
# This is the local counterpart to build.sh + install.sh: build.sh/install.sh produce a
# release and deploy it as systemd quadlets, whereas this script builds straight into
# podman and runs the containers in a single pod, so it works on a plain WSL Ubuntu
# without user-systemd or release artifacts. It always runs in DEBUG mode: the proxy
# serves plain HTTP on port 8080, so no TLS certificate is needed.
#
# The container wiring (images, environment, secrets, volumes) is kept identical to the
# quadlets in quadlets/ so dev and production behave the same; only DEBUG and the proxy
# port differ.
set -e

cd "$(dirname "$0")"

# Build-time settings (image names, app name, paths). DEBUG is forced on below.
set -a
. ./buildtime.env
set +a

POD="${APP_NAME}"
# Plain HTTP for local development; mapped to the proxy's port 80 inside the pod. 8080
# avoids needing privileged ports, which rootless podman does not grant by default.
HTTP_PORT="${HTTP_PORT:-8080}"
# Publish PostgreSQL on the host too, so a local psql or DB GUI can connect. The
# in-pod port stays 5432; only the host port is exposed.
PG_PORT="${PG_PORT:-5432}"
PG_USER="${SUPERUSER_NAME}"
PG_DB="postgres"
# Publish on the IPv4 loopback explicitly. On WSL "localhost" often resolves to ::1
# first, which podman's pasta networking does not bind, so binding 127.0.0.1 keeps the
# printed URLs reachable. Use this address in the summary for the same reason.
HOST_ADDR="127.0.0.1"
SERVICES="postgresql crudman sqlmesh proxy grafana"

# The sampling cadence for the background loop below, read from runtime.env like the
# collector does (default 60s). Kept in one place so `serverstats` and the loop agree.
SERVER_STATS_INTERVAL="${SERVER_STATS_INTERVAL:-60}"
# Where the loop's PID is recorded so a later run or `down` can stop it. Under the same
# state dir the collector already uses, so dev leaves nothing outside XDG paths.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/${APP_NAME}"
STATS_PIDFILE="$STATE_DIR/dev-serverstats.pid"

# Run the collector once against the dev stack. The deployment runs this on a systemd
# timer; locally there is no user-systemd, so it is invoked directly. POSTGRES_USER lets it
# authenticate as the dev superuser, and RUNTIME_ENV=/dev/null skips the runtime.env lookup
# so the interval passed in the environment (or the default) applies.
run_collector_once() {
  POSTGRES_USER="$PG_USER" SERVER_STATS_SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}" \
    RUNTIME_ENV=/dev/null ./serverstats/collect.sh
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
# Same Dockerfiles and proxy build-args as build.sh, but built with podman so the images
# land directly in the local rootless store the containers run from. podman's layer cache
# makes re-runs cheap: a service whose Dockerfile and inputs are unchanged reuses its
# cached layers, so a plain `./dev.sh` on a running stack is a quick refresh rather than a
# full rebuild. Build output is quiet so a cached run does not look like real work; drop
# the redirection on a line below to see a failing build's full log.
echo "Building images ..."

# Render the Grafana provisioning templates the grafana Dockerfile COPYs in, exactly as
# build.sh does; without this the COPY of grafana/.provisioning/ has no source. The output
# is deterministic, so an unchanged dashboard keeps the grafana COPY layer cached.
./grafana/render.sh grafana/.provisioning

for svc in $SERVICES; do
  printf '  %-11s ' "$svc"
  build_svc() {
    podman build \
      --build-arg "http_proxy=${HTTP_PROXY}" \
      --build-arg "https_proxy=${HTTPS_PROXY}" \
      --build-arg "no_proxy=${NO_PROXY}" \
      -t "${REGISTRY}/${svc}:${IMAGE_TAG}" \
      -f "${svc}/Dockerfile" .
  }
  # Keep the happy path quiet; on failure re-run the same build so its full log is shown,
  # then abort (set -e alone would swallow the log we redirected away).
  if build_svc >/dev/null 2>&1; then
    echo "ok"
  else
    echo "FAILED"
    build_svc
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
create_secret django_secret_key "$(openssl rand -hex 32)"
create_secret crudman_password  "$(openssl rand -hex 32)"
create_secret sqlmesh_password  "$(openssl rand -hex 32)"
create_secret grafana_password  "$(openssl rand -hex 32)"

# The superuser login is a fixed, well-known value (SUPERUSER_DEFAULT_PASSWORD from
# buildtime.env) so the stack comes up unattended and the printed credentials are always
# correct. Unlike the secrets above it is set on every run (replacing any earlier value,
# e.g. one a previous install prompted for), because the crudman entrypoint resets the
# superuser password to this secret on start. This is a dev-only convenience and not for
# production.
podman secret rm superuser_password >/dev/null 2>&1 || true
printf '%s' "$SUPERUSER_DEFAULT_PASSWORD" | podman secret create superuser_password - >/dev/null

# --- volumes --------------------------------------------------------------------------
# Created up front so the rootless user owns their contents from the start (same reason as
# install.sh), one per service matching the *.volume quadlets.
for vol in postgresql_data grafana_data crudman_data sqlmesh_data proxy_data; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done

# --- (re)create the pod ---------------------------------------------------------------
# A fresh pod each run keeps things reproducible; the data lives in the volumes, not the
# containers, so this loses nothing. Only the proxy publishes a port, at the pod level,
# exactly as main.pod does.
podman pod rm -f "$POD" >/dev/null 2>&1 || true
podman pod create --name "$POD" \
  --publish "${HOST_ADDR}:${HTTP_PORT}:80" \
  --publish "${HOST_ADDR}:${PG_PORT}:5432" >/dev/null

# --- run the containers ---------------------------------------------------------------
# Each `podman run` mirrors the matching *.container quadlet: same image, environment,
# secrets and data volume. The containers share the pod's network namespace, so they
# reach each other on localhost just like the quadlet deployment.

podman run -d --pod "$POD" --name postgresql --restart always \
  -e POSTGRES_DB=postgres \
  -e "POSTGRES_USER=${SUPERUSER_NAME}" \
  -e POSTGRES_PASSWORD_FILE=/run/secrets/superuser_password \
  -v postgresql_data:/var/lib/postgresql/data \
  --secret superuser_password --secret crudman_password \
  --secret sqlmesh_password --secret grafana_password \
  --health-cmd "pg_isready -U ${SUPERUSER_NAME} -d postgres" \
  --health-interval 5s --health-retries 10 --health-start-period 10s \
  "${REGISTRY}/postgresql:${IMAGE_TAG}" >/dev/null

podman run -d --pod "$POD" --name crudman --restart always \
  -e "APP_NAME=${APP_NAME}" \
  -e "SERVER_NAME=${SERVER_NAME}" \
  -e "SUPERUSER_NAME=${SUPERUSER_NAME}" \
  -e "SUPERUSER_EMAIL=${SUPERUSER_EMAIL}" \
  -e "CRUDMAN_PATH=${CRUDMAN_PATH}" \
  -e DEBUG=true \
  -e "CSRF_TRUSTED_ORIGINS=http://${HOST_ADDR}:${HTTP_PORT}" \
  -e POSTGRES_HOST=localhost -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=postgres -e POSTGRES_USER=crudman \
  -v crudman_data:/var/log/gefieder \
  --secret django_secret_key --secret crudman_password --secret superuser_password \
  "${REGISTRY}/crudman:${IMAGE_TAG}" >/dev/null

podman run -d --pod "$POD" --name sqlmesh --restart always \
  -e POSTGRES_HOST=localhost -e POSTGRES_PORT=5432 -e POSTGRES_DB=postgres \
  -e SQLMESH_RUN_INTERVAL=10 \
  -v sqlmesh_data:/var/log/gefieder \
  --secret sqlmesh_password \
  "${REGISTRY}/sqlmesh:${IMAGE_TAG}" >/dev/null

podman run -d --pod "$POD" --name grafana --restart always \
  -e "APP_NAME=${APP_NAME}" \
  -e "GF_SECURITY_ADMIN_USER=${SUPERUSER_NAME}" \
  -e GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/superuser_password \
  -e "GF_SERVER_ROOT_URL=%(protocol)s://%(domain)s/${GRAFANA_PATH}/" \
  -e GF_SERVER_SERVE_FROM_SUB_PATH=true \
  -e "GF_LOG_MODE=console file" \
  -e GF_PATHS_LOGS=/var/lib/grafana/log \
  -v grafana_data:/var/lib/grafana \
  --secret superuser_password --secret grafana_password \
  "${REGISTRY}/grafana:${IMAGE_TAG}" >/dev/null

# DEBUG=true makes the proxy entrypoint pick the plain-HTTP template, so the certs mount
# the quadlet uses is unnecessary and omitted here.
podman run -d --pod "$POD" --name proxy --restart always \
  -e DEBUG=true \
  -e "CRUDMAN_PATH=${CRUDMAN_PATH}" \
  -e "GRAFANA_PATH=${GRAFANA_PATH}" \
  -v proxy_data:/var/log/gefieder \
  "${REGISTRY}/proxy:${IMAGE_TAG}" >/dev/null

# --- background server-statistics sampling --------------------------------------------
# Replace the systemd timer the deployment uses with a background loop, so server_stats
# fills automatically in dev too. Started here, after the containers are up, so the first
# sample has a database to write to; stopped by `./dev.sh down`.
start_stats_loop

# --- summary --------------------------------------------------------------------------
cat <<EOF

${APP_NAME} is starting in development mode (plain HTTP, no certificate).

  Admin panel:  http://${HOST_ADDR}:${HTTP_PORT}/${CRUDMAN_PATH}/
  Grafana:      http://${HOST_ADDR}:${HTTP_PORT}/${GRAFANA_PATH}/
  Login:        ${SUPERUSER_NAME} / ${SUPERUSER_DEFAULT_PASSWORD}

  PostgreSQL:   host=${HOST_ADDR} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER}
                (password = the superuser password, same as the admin login)
                psql "host=${HOST_ADDR} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER}"

  Follow logs:  ./dev.sh logs
  Stop:         ./dev.sh down

Server statistics are sampled every ${SERVER_STATS_INTERVAL}s in the background (the
server_stats schema and the Grafana monitoring dashboard fill on their own). Run one sample
now with ./dev.sh serverstats.

The database needs a few seconds to initialise on the first run.
EOF
