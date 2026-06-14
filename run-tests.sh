#!/bin/sh
# Bring up a throwaway Gefieder stack, run the integration test suite against it and
# tear it down again. Meant to run in isolation, far from any production system: it
# starts a fresh stack (so the database is empty) and removes it afterwards.
#
#   ./run-tests.sh             test the dev profile (DEBUG=true, plain HTTP)
#   ./run-tests.sh production  test the production profile (DEBUG=false, HTTPS)
set -e

PROFILE="${1:-dev}"

# Load the .env settings so the suite tests the configured stack (CRUDMAN_PATH,
# GRAFANA_PATH, APP_NAME, SUPERUSER_NAME, ...) rather than assuming the defaults.
# "set -a" exports every variable assigned while sourcing.
set -a
. ./.env
set +a

# Run under a dedicated compose project name so the test stack's named volumes are
# separate from a real deployment's (gefiedertest_postgresql vs gefieder_postgresql).
# The teardown removes the test volumes with "down -v"; production volumes are never
# touched. Every podman-compose call below must pass this via -p.
PROJECT=gefiedertest

# Isolated host ports so a running stack on the default ports is not disturbed.
export HTTP_PORT=18080
export HTTPS_PORT=18443
export GRAFANA_PORT=13000
export PG_PORT=15432

# The suite connects to the database as each role to check its access boundary; the
# passwords come from the podman secrets that exist on the test host.
GRAFANA_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' grafana_password)"
SUPERUSER_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' superuser_password)"
CRUDMAN_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' crudman_password)"
SQLMESH_PASSWORD="$(podman secret inspect --showsecret -f '{{.SecretData}}' sqlmesh_password)"

if [ "$PROFILE" = "production" ]; then
  export DEBUG=false
  SCHEME=https
  APP_PORT="$HTTPS_PORT"
  # A self-signed certificate just for this test run; removed on teardown.
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=localhost" \
    -keyout proxy/certs/privkey.pem -out proxy/certs/fullchain.pem >/dev/null 2>&1
else
  export DEBUG=true
  SCHEME=http
  APP_PORT="$HTTP_PORT"
fi

cleanup() {
  # "-v" also removes the test stack's named volumes, so each run starts from an empty
  # database. Safe because -p scopes this to the dedicated test project's volumes.
  podman-compose -p "$PROJECT" down -v >/dev/null 2>&1 || true
  rm -f proxy/certs/fullchain.pem proxy/certs/privkey.pem
}
trap cleanup EXIT INT TERM

export GEFIEDER_PROFILE="$PROFILE"
export GEFIEDER_BASE_URL="$SCHEME://localhost:$APP_PORT"
export GEFIEDER_HTTP_BASE_URL="http://localhost:$HTTP_PORT"
export GEFIEDER_PG_PORT="$PG_PORT"
export GEFIEDER_GRAFANA_PASSWORD="$GRAFANA_PASSWORD"
export GEFIEDER_SUPERUSER_PASSWORD="$SUPERUSER_PASSWORD"
export GEFIEDER_CRUDMAN_PASSWORD="$CRUDMAN_PASSWORD"
export GEFIEDER_SQLMESH_PASSWORD="$SQLMESH_PASSWORD"

# Build fresh images so the test exercises exactly what would be deployed.
podman-compose -p "$PROJECT" up -d --build

# Run the suite. uv provides the test dependencies from tests/pyproject.toml.
uv run --project tests pytest tests/ -v
