#!/bin/sh
# Bring up a throwaway Gefieder stack from the quadlets, run the integration test suite
# against it and tear it down again. Meant to run in isolation, far from any production
# system: it builds fresh images, starts a stack with an empty database under the project
# pod name and isolated ports, and removes it afterwards.
#
#   ./run-tests.sh             test the dev profile (DEBUG=true, plain HTTP)
#   ./run-tests.sh production  test the production profile (DEBUG=false, HTTPS)
set -e

PROFILE="${1:-dev}"
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Load the build-time settings so the suite tests the configured stack (CRUDMAN_PATH,
# GRAFANA_PATH, APP_NAME, SUPERUSER_NAME, ...) rather than assuming the defaults.
set -a
. ./buildtime.env
set +a

# Isolated host ports so a running stack on the default ports is not disturbed.
HTTP_PORT=18080
HTTPS_PORT=18443
GRAFANA_PORT=13000
PG_PORT=15432

# The host-local cert directory the proxy quadlet mounts (same path a deployment uses).
# It must exist even in dev mode, where the proxy mounts but does not read it.
CERT_DIR="$HOME/.config/${APP_NAME}/certs"
mkdir -p "$CERT_DIR"

# Build the custom images with podman, the engine that runs the quadlets, so the suite
# exercises exactly what a deployment runs. (build.sh builds with docker for the release
# workflow; docker and podman keep separate image stores, so a docker build would not be
# visible to the podman-run stack here.) Tagged REGISTRY/<svc>:IMAGE_TAG to match the
# Image= lines in the quadlets; built from the working tree, not pulled.
for svc in postgresql crudman sqlmesh proxy grafana; do
  podman build -t "${REGISTRY}/${svc}:${IMAGE_TAG}" -f "${svc}/Dockerfile" .
done

# The suite connects to the database as each role to check its access boundary; the
# passwords come from the podman secrets that exist on the test host.
GRAFANA_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' grafana_password)"
SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' superuser_password)"
CRUDMAN_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' crudman_password)"
SQLMESH_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' sqlmesh_password)"

if [ "$PROFILE" = "production" ]; then
  DEBUG=false
  SCHEME=https
  APP_PORT="$HTTPS_PORT"
  # A self-signed certificate just for this test run, in the host-local cert dir the
  # proxy quadlet mounts; removed on teardown.
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" >/dev/null 2>&1
else
  DEBUG=true
  SCHEME=http
  APP_PORT="$HTTP_PORT"
fi
export DEBUG

# A running "systemd --user" only scans this fixed path with its generator (it ignores an
# XDG_CONFIG_HOME we might export here), so the test must install where the real
# deployment installs. Refuse to run if a deployment is already there, so the test never
# clobbers it.
QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
mkdir -p "$QUADLET_DIR"
if [ -e "$QUADLET_DIR/main.pod" ]; then
  echo "A deployment is already installed in $QUADLET_DIR; refusing to run." >&2
  echo "Stop and remove it first, or run the tests on a host without a deployment." >&2
  exit 1
fi

# Render the quadlet templates the same way the release workflow does: substitute only
# the known tokens so nginx's $host and Grafana's %(domain)s are left untouched.
VARS='${REGISTRY} ${IMAGE_TAG} ${APP_NAME} ${SERVER_NAME} ${SUPERUSER_NAME} ${SUPERUSER_EMAIL} ${CRUDMAN_PATH} ${GRAFANA_PATH} ${DEBUG}'
for f in quadlets/*; do
  envsubst "$VARS" < "$f" > "$QUADLET_DIR/$(basename "$f")"
done

# Quadlet does not expand variables in PublishPort, so overwrite the rendered pod file
# with the isolated test ports. The test pod also publishes the database and Grafana
# ports (the production pod publishes only 80/443) so the suite reaches them on
# localhost directly. PodName stays ${APP_NAME} so podman shows the project name.
cat > "$QUADLET_DIR/main.pod" <<EOF
[Pod]
PodName=${APP_NAME}
PublishPort=${HTTP_PORT}:80
PublishPort=${HTTPS_PORT}:443
PublishPort=${PG_PORT}:5432
PublishPort=${GRAFANA_PORT}:3000

[Install]
WantedBy=default.target
EOF

UNITS="postgresql crudman sqlmesh grafana proxy"
VOLUMES="postgresql_data grafana_data crudman_data sqlmesh_data proxy_data"

cleanup() {
  for u in $UNITS; do systemctl --user stop "${u}.service" >/dev/null 2>&1 || true; done
  podman pod rm -f "$APP_NAME" >/dev/null 2>&1 || true
  podman volume rm -f $VOLUMES >/dev/null 2>&1 || true
  # Remove exactly the unit files rendered above (one per quadlets/ entry), so renames
  # never leave stragglers behind.
  for f in quadlets/*; do rm -f "$QUADLET_DIR/$(basename "$f")"; done
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -f "$CERT_DIR/fullchain.pem" "$CERT_DIR/privkey.pem"
}
trap cleanup EXIT INT TERM

systemctl --user daemon-reload
# Starting the proxy pulls in the rest of the pod (After=/Requires=), but start every
# unit explicitly so a failure in any one surfaces here rather than being masked.
for u in $UNITS; do systemctl --user start "${u}.service"; done

export GEFIEDER_PROFILE="$PROFILE"
export GEFIEDER_BASE_URL="$SCHEME://localhost:$APP_PORT"
export GEFIEDER_HTTP_BASE_URL="http://localhost:$HTTP_PORT"
export GEFIEDER_PG_PORT="$PG_PORT"
export GEFIEDER_GRAFANA_PASSWORD="$GRAFANA_PASSWORD"
export GEFIEDER_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD"
export GEFIEDER_CRUDMAN_PASSWORD="$CRUDMAN_PASSWORD"
export GEFIEDER_SQLMESH_PASSWORD="$SQLMESH_PASSWORD"

# Run the suite. uv provides the test dependencies from tests/pyproject.toml.
uv run --project tests pytest tests/ -v
