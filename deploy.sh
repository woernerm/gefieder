#!/bin/sh
# Deploy the system on a server, without a repository checkout. Everything comes from
# the registry: the images, and the quadlet units as an OCI artifact. The only
# host-local pieces are the podman secrets and the TLS certificate.
#
#   ./deploy.sh <registry>
#   ./deploy.sh ghcr.io/your-org/gefieder
#
# Prerequisites (one-time, see the README): podman >= 5.0, the rootless port and
# firewall settings applied, and the certificate placed in ~/.config/gefieder/certs/
# (fullchain.pem and privkey.pem). If the registry is private, run `podman login` first.
set -e

REGISTRY="${1:?usage: deploy.sh <registry>, e.g. ghcr.io/your-org/gefieder}"
TAG="${2:-latest}"
QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
CERT_DIR="$HOME/.config/gefieder/certs"

if [ ! -f "$CERT_DIR/fullchain.pem" ] || [ ! -f "$CERT_DIR/privkey.pem" ]; then
  echo "Missing TLS certificate in $CERT_DIR (fullchain.pem and privkey.pem)." >&2
  echo "Production serves HTTPS only and needs it; see the README." >&2
  exit 1
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

# Pull the pre-rendered quadlets from the registry and extract them into the systemd
# user dir. They are already rendered (the build did it), so there is nothing to
# template here and no .env is needed.
ART="${REGISTRY}/quadlets:${TAG}"
podman artifact pull "$ART"
mkdir -p "$QUADLET_DIR"
podman artifact extract "$ART" "$QUADLET_DIR"

loginctl enable-linger "$USER" >/dev/null 2>&1 || true
systemctl --user daemon-reload
systemctl --user start proxy.service

# Self-updating: pull and roll back new images, and dump the database daily.
systemctl --user enable --now podman-auto-update.timer
systemctl --user enable --now backup.timer

echo
echo "Deployed from $REGISTRY. Verify in a browser (HTTP is redirected to HTTPS)."
