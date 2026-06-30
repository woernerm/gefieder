#!/bin/sh
# Build the five service images with docker, the same way CI does, so a local build and
# the release build stay identical. The CI workflow calls this script too.
#
#   ./build.sh
#
# Settings come from buildtime.env: REGISTRY/IMAGE_TAG name the images
# (REGISTRY/<svc>:IMAGE_TAG, matching the Image= lines in the quadlets), the
# HTTP(S)_PROXY/NO_PROXY values are passed as --build-arg so package installs work from
# behind a company proxy and PYTHON_INDEX adds a company PyPI mirror for uv.
set -e

cd "$(dirname "$0")"
set -a
. ./buildtime.env
set +a

# The entrypoints are committed executable and the Dockerfiles use plain COPY, so the
# build needs no BuildKit-only features and works on any docker (classic or BuildKit).
for svc in postgresql crudman sqlmesh proxy grafana; do
  docker build \
    --build-arg "http_proxy=${HTTP_PROXY}" \
    --build-arg "https_proxy=${HTTPS_PROXY}" \
    --build-arg "no_proxy=${NO_PROXY}" \
    --build-arg "PYTHON_INDEX=${PYTHON_INDEX}" \
    --build-arg "SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}" \
    -t "${REGISTRY}/${svc}:${IMAGE_TAG}" \
    -f "${svc}/Dockerfile" .
done
