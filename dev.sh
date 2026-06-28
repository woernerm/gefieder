#!/bin/sh
# Build and run the whole stack locally with rootless podman, in development mode.
#
#   ./dev.sh            build the images and (re)start the stack
#   ./dev.sh down       stop and remove the pod (volumes and secrets are kept)
#   ./dev.sh logs       follow the combined logs of all containers
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

# --- subcommands ----------------------------------------------------------------------
case "${1:-up}" in
  down)
    podman pod rm -f "$POD" >/dev/null 2>&1 || true
    echo "Stopped and removed pod '$POD'. Volumes and secrets are kept."
    exit 0
    ;;
  logs)
    # Follow every container's log at once; podman prefixes each line with the name.
    exec podman pod logs -f "$POD"
    ;;
  up) ;;
  *)
    echo "usage: $0 [up|down|logs]" >&2
    exit 1
    ;;
esac

# --- build the images -----------------------------------------------------------------
# Same Dockerfiles and proxy build-args as build.sh, but built with podman so the images
# land directly in the local rootless store the containers run from.
echo "Building images ..."
for svc in $SERVICES; do
  podman build \
    --build-arg "http_proxy=${HTTP_PROXY}" \
    --build-arg "https_proxy=${HTTPS_PROXY}" \
    --build-arg "no_proxy=${NO_PROXY}" \
    -t "${REGISTRY}/${svc}:${IMAGE_TAG}" \
    -f "${svc}/Dockerfile" .
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

# The superuser login is a fixed, well-known value ("admin") so the stack comes up
# unattended and the printed credentials are always correct. Unlike the secrets above it
# is set on every run (replacing any earlier value, e.g. one a previous install prompted
# for), because the crudman entrypoint resets the superuser password to this secret on
# start. This is a dev-only convenience and not for production.
podman secret rm superuser_password >/dev/null 2>&1 || true
printf '%s' "admin" | podman secret create superuser_password - >/dev/null

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

# --- summary --------------------------------------------------------------------------
cat <<EOF

${APP_NAME} is starting in development mode (plain HTTP, no certificate).

  Admin panel:  http://${HOST_ADDR}:${HTTP_PORT}/${CRUDMAN_PATH}/
  Grafana:      http://${HOST_ADDR}:${HTTP_PORT}/${GRAFANA_PATH}/
  Login:        ${SUPERUSER_NAME} / admin

  PostgreSQL:   host=${HOST_ADDR} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER}
                (password = the superuser password, same as the admin login)
                psql "host=${HOST_ADDR} port=${PG_PORT} dbname=${PG_DB} user=${PG_USER}"

  Follow logs:  ./dev.sh logs
  Stop:         ./dev.sh down

The database needs a few seconds to initialise on the first run.
EOF
