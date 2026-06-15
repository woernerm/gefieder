#!/bin/sh
# Bring up the full system locally in development mode (plain HTTP, no certificates) so
# you can try it out. Creates any missing secrets, builds the images, switches .env to
# DEBUG=true and starts the pod. Re-runnable: existing secrets and images are reused.
#
#   ./run-dev.sh        start the dev stack
#   ./run-dev.sh down   stop the stack
set -e

cd "$(dirname "$0")"
UNITS="postgresql crudman sqlmesh grafana proxy"

if [ "$1" = "down" ]; then
  for u in $UNITS; do systemctl --user stop "${u}.service" >/dev/null 2>&1 || true; done
  echo "Stopped. Data is kept in the named volumes."
  exit 0
fi

# Create any missing secrets. superuser_password is prompted for, the rest are random.
ensure_secret() { podman secret exists "$1" || eval "$2" | podman secret create "$1" -; }
ensure_secret django_secret_key "openssl rand -hex 64"
ensure_secret crudman_password  "openssl rand -hex 32"
ensure_secret sqlmesh_password  "openssl rand -hex 32"
ensure_secret grafana_password  "openssl rand -hex 32"
if ! podman secret exists superuser_password; then
  printf "Choose a superuser password: " >&2
  read -rs PW; echo >&2
  printf "%s" "$PW" | podman secret create superuser_password -
fi

./build.sh

# The proxy mounts the cert dir even in dev (it just is not read over plain HTTP), so
# make sure it exists. Select development mode in .env, render the quadlets from it, and
# start the pod (the proxy pulls in the rest).
mkdir -p "$HOME/.config/gefieder/certs"
sed -i 's/^DEBUG=.*/DEBUG=true/' .env
./install.sh
systemctl --user daemon-reload
systemctl --user start proxy.service

echo
echo "Up. Log in as admin with your superuser password:"
echo "  Administration panel: http://localhost/crudman/"
echo "  Grafana dashboards:   http://localhost/grafana/"
echo "Stop with: ./run-dev.sh down"
