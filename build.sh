#!/bin/sh
# Build the five custom images and tag them into the configured registry namespace
# (REGISTRY/IMAGE_TAG from .env). With "push" as the first argument, also push them and
# the quadlet OCI artifact, which is how the server gets its units without a checkout.
#
#   ./build.sh        build and tag locally
#   ./build.sh push   build, tag and push the images and the quadlet artifact
set -e

cd "$(dirname "$0")"
set -a
. ./.env
set +a

# postgresql/crudman/sqlmesh are app images; proxy and grafana bake in the config that
# used to be bind-mounted, so the server needs no checkout for it.
for svc in postgresql crudman sqlmesh proxy grafana; do
  podman build -t "${REGISTRY}/${svc}:${IMAGE_TAG}" -f "./${svc}/Dockerfile" .
  if [ "$1" = "push" ]; then
    podman push "${REGISTRY}/${svc}:${IMAGE_TAG}"
  fi
done

# Package the quadlets, rendered from .env, as an OCI artifact and push it. The server
# pulls and extracts this into its systemd dir, so it needs neither a clone nor .env.
if [ "$1" = "push" ]; then
  STAGE="$(mktemp -d)"
  ./install.sh "$STAGE" >/dev/null
  ART="${REGISTRY}/quadlets:${IMAGE_TAG}"
  podman artifact rm "$ART" >/dev/null 2>&1 || true
  podman artifact add "$ART" "$STAGE"/*
  podman artifact push "$ART"
  rm -rf "$STAGE"
fi
