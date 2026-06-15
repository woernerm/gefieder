#!/bin/sh
# Render the quadlet templates from quadlets/ into the user's quadlet directory,
# substituting the settings from .env. Quadlet itself does not expand variables in keys
# like Image=, HealthCmd= or PublishPort=, so the substitution happens here at install
# time instead. Run this once after editing .env, then reload systemd:
#
#   ./install.sh && systemctl --user daemon-reload
#
# A target directory can be passed as the first argument (the test suite uses this); it
# defaults to the standard rootless quadlet path.
set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd}"

set -a
. "$REPO/.env"
set +a

# Only these variables are substituted, so that nginx's $host or Grafana's %(domain)s
# are never touched (and an accidental $VAR in a unit fails loudly instead of vanishing).
VARS='${REGISTRY} ${IMAGE_TAG} ${APP_NAME} ${SERVER_NAME} ${SUPERUSER_NAME} ${SUPERUSER_EMAIL} ${CRUDMAN_PATH} ${GRAFANA_PATH} ${DEBUG}'

mkdir -p "$DEST"
for f in "$REPO"/quadlets/*; do
  # The pod and volume files carry an APP_NAME token in their names so the generated
  # pod/volume units are prefixed with the project name; render it in the filename too.
  name="$(basename "$f" | sed "s/APP_NAME/${APP_NAME}/")"
  envsubst "$VARS" < "$f" > "$DEST/$name"
done

echo "Installed the $APP_NAME quadlets into $DEST."
echo "Run 'systemctl --user daemon-reload' to pick them up."
