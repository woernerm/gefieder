#!/bin/sh
# Read-only smoke tests against the *running production system*. These never write to
# the database or touch volumes, so they are safe to run against live production. They
# confirm the stack is actually serving after a deployment; if any check fails the
# deploy script rolls back.
#
# Run from the repository root, after the production stack is up. Reads .env for the
# configured paths.
set -e

set -a
. ./.env
set +a

CRUDMAN_PATH="${CRUDMAN_PATH:-crudman}"
GRAFANA_PATH="${GRAFANA_PATH:-grafana}"

fail() { echo "SMOKE FAIL: $1" >&2; exit 1; }

echo "Smoke test: containers are running and healthy"
for c in postgresql crudman sqlmesh grafana proxy; do
  state="$(podman inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
  [ "$state" = "running" ] || fail "container $c is '$state', expected running"
done
for c in postgresql crudman; do
  health="$(podman inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo none)"
  [ "$health" = "healthy" ] || fail "container $c health is '$health', expected healthy"
done

echo "Smoke test: the database accepts connections"
podman exec postgresql pg_isready -U "${SUPERUSER_NAME:-admin}" -d postgres >/dev/null \
  || fail "postgresql is not ready"

echo "Smoke test: the proxy redirects plain HTTP to HTTPS"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost/${CRUDMAN_PATH}/" || true)"
[ "$code" = "301" ] || fail "http://localhost/${CRUDMAN_PATH}/ returned $code, expected 301"

echo "Smoke test: the admin panel is served over HTTPS"
code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost/${CRUDMAN_PATH}/login/" || true)"
[ "$code" = "200" ] || fail "crudman login returned $code, expected 200"

echo "Smoke test: Grafana is served over HTTPS"
code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost/${GRAFANA_PATH}/login" || true)"
[ "$code" = "200" ] || fail "grafana login returned $code, expected 200"

echo "All smoke tests passed."
