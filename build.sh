#!/bin/sh
# Build the five service images with docker, the same way CI does, so a local build and
# the release build stay identical. The CI workflow calls this script too.
#
#   ./build.sh
#
# Settings come from buildtime.env: REGISTRY/IMAGE_TAG name the images
# (REGISTRY/<svc>:IMAGE_TAG, matching the Image= lines in the quadlets), the
# HTTP(S)_PROXY/NO_PROXY values are passed as --build-arg so package installs work from
# behind a company proxy, PYTHON_INDEX adds a company PyPI mirror for uv, the *_MIRROR
# values name the registries the base images are pulled from, and DUCKDB_EXTENSIONS and
# GRAFANA_PLUGINS select what is baked into the PostgreSQL and Grafana images.
set -e

cd "$(dirname "$0")"
set -a
. ./buildtime.env
set +a

# Render the templated parts of two images into temporary directories their Dockerfiles
# COPY in. Grafana gets APP_NAME and SERVER_STATS_SCHEMA baked into its dashboard JSON,
# which Grafana itself cannot interpolate; PostgreSQL gets the database role names baked
# into its init scripts, which psql cannot interpolate inside a function body.
./grafana/render.sh grafana/.render
./postgresql/render.sh postgresql/.initdb

# The entrypoints are committed executable and the Dockerfiles use plain COPY, so the
# build needs no BuildKit-only features and works on any docker (classic or BuildKit).
for svc in postgresql crudman sqlmesh proxy grafana; do
  docker build \
    --build-arg "http_proxy=${HTTP_PROXY}" \
    --build-arg "https_proxy=${HTTPS_PROXY}" \
    --build-arg "no_proxy=${NO_PROXY}" \
    --build-arg "PYTHON_INDEX=${PYTHON_INDEX}" \
    --build-arg "DOCKER_IO_MIRROR=${DOCKER_IO_MIRROR}" \
    --build-arg "GHCR_IO_MIRROR=${GHCR_IO_MIRROR}" \
    --build-arg "SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}" \
    --build-arg "SECRET_SUPERUSER_PASSWORD=${SECRET_SUPERUSER_PASSWORD}" \
    --build-arg "DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}" \
    --build-arg "GRAFANA_PLUGINS=${GRAFANA_PLUGINS}" \
    -t "${REGISTRY}/${svc}:${IMAGE_TAG}" \
    -f "${svc}/Dockerfile" .
done
