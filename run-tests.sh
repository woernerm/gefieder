#!/bin/sh
# Bring up a throwaway Gefieder stack from the quadlets, run the integration test suite
# against it and tear it down again. Meant to run in isolation, far from any production
# system: it builds fresh images, starts a stack with an empty database under an
# isolated pod name and ports, and removes it afterwards.
#
#   ./run-tests.sh             test the dev profile (DEBUG=true, plain HTTP)
#   ./run-tests.sh production  test the production profile (DEBUG=false, HTTPS)
set -e

PROFILE="${1:-dev}"
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# Load the .env settings so the suite tests the configured stack (CRUDMAN_PATH,
# GRAFANA_PATH, APP_NAME, SUPERUSER_NAME, ...) rather than assuming the defaults.
set -a
. ./.env
set +a

# Isolated host ports so a running stack on the default ports is not disturbed.
export HTTP_PORT=18080
export HTTPS_PORT=18443
export GRAFANA_PORT=13000
export PG_PORT=15432

# The host-local cert directory the proxy quadlet mounts (same path a deployment uses).
# It must exist even in dev mode, where the proxy mounts but does not read it.
CERT_DIR="$HOME/.config/gefieder/certs"
mkdir -p "$CERT_DIR"

# Build and tag the custom images locally (not pushed), so the quadlets find them
# without pulling and the suite exercises the working-tree code, not a pushed image.
./build.sh

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
  mkdir -p "$CERT_DIR"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" >/dev/null 2>&1
else
  DEBUG=true
  SCHEME=http
  APP_PORT="$HTTP_PORT"
fi

# install.sh renders DEBUG into the quadlets from .env, so select the profile by
# rewriting the file's DEBUG line for the run. The original .env is restored on teardown.
cp .env .env.testbak
sed -i "s/^DEBUG=.*/DEBUG=${DEBUG}/" .env

# Install the quadlets into the user's quadlet directory. A running "systemd --user"
# only scans this fixed path with its generator (it ignores an XDG_CONFIG_HOME we might
# export here), so the test must install where the real deployment installs. Refuse to
# run if a deployment is already there, so the test never clobbers it.
QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
if [ -e "$QUADLET_DIR/${APP_NAME}.pod" ]; then
  echo "A $APP_NAME deployment is already installed in $QUADLET_DIR; refusing to run." >&2
  echo "Stop and remove it first, or run the tests on a host without a deployment." >&2
  exit 1
fi
# Render the templates from .env into the quadlet dir, the same way a deployment does.
./install.sh "$QUADLET_DIR" >/dev/null

# Quadlet does not expand variables in PublishPort, so overwrite the rendered pod file
# with the isolated test ports. The test pod also publishes the database and Grafana
# ports (the production pod publishes only 80/443) so the suite reaches them on
# localhost directly.
cat > "$QUADLET_DIR/${APP_NAME}.pod" <<EOF
[Pod]
PodName=${APP_NAME}
PublishPort=${HTTP_PORT}:80
PublishPort=${HTTPS_PORT}:443
PublishPort=${PG_PORT}:5432
PublishPort=${GRAFANA_PORT}:3000
EOF

UNITS="postgresql crudman sqlmesh grafana proxy"

cleanup() {
  for u in $UNITS; do systemctl --user stop "${u}.service" >/dev/null 2>&1 || true; done
  podman pod rm -f "$APP_NAME" >/dev/null 2>&1 || true
  podman volume rm -f "${APP_NAME}-postgresql" "${APP_NAME}-grafana" >/dev/null 2>&1 || true
  # Remove exactly the unit files install.sh placed (it mirrors quadlets/, applying the
  # same APP_NAME filename substitution), so renames never leave stragglers behind.
  for f in quadlets/*; do
    rm -f "$QUADLET_DIR/$(basename "$f" | sed "s/APP_NAME/${APP_NAME}/")"
  done
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -f "$CERT_DIR/fullchain.pem" "$CERT_DIR/privkey.pem"
  [ -f .env.testbak ] && mv .env.testbak .env
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
