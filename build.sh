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

# SERVICES, render_build_templates and build_image.
. ./build-lib.sh

render_build_templates

# The entrypoints are committed executable and the Dockerfiles use plain COPY, so the
# build needs no BuildKit-only features and works on any docker (classic or BuildKit).
for svc in $SERVICES; do
  build_image docker "$svc"
done
