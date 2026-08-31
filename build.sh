#!/bin/sh
# Build the five service images with docker. The CI workflow calls this script too, so a
# local build and the release build stay identical.
#
#   ./build.sh
#
# Settings come from buildtime.env: REGISTRY and IMAGE_TAG name the images to match the
# quadlets' Image= lines, the proxy values let package installs through a company proxy,
# PYTHON_INDEX adds a PyPI mirror, the *_MIRROR values name the base image registries, and
# DUCKDB_EXTENSIONS and GRAFANA_PLUGINS select what is baked in.
set -e

cd "$(dirname "$0")"
set -a
. ./buildtime.env
set +a

# SERVICES, render_build_templates and build_image.
. ./build-lib.sh

render_build_templates

# The entrypoints are committed executable and the Dockerfiles use plain COPY, so this
# needs no BuildKit-only features.
for svc in $SERVICES; do
  build_image docker "$svc"
done
