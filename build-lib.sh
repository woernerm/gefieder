#!/bin/sh
# The image build, shared by build.sh (release, docker), dev.sh and run-tests.sh (podman).
# Sourced, not executed, after buildtime.env is in the environment:
#
#   . ./build-lib.sh
#   render_build_templates
#   for svc in $SERVICES; do build_image podman "$svc"; done
#
# Every --build-arg below is a buildtime.env setting the image bakes in, and one left out
# falls back to the Dockerfile's ARG default -- building an image the rest of the stack
# disagrees with (SERVER_STATS_SCHEMA decides which schema the database creates). Keeping
# the list here means there is one place to add the next one.
#
# What differs between the callers stays with them: which engine, and what to do with the
# output. dev.sh hides a successful build and replays a failed one; the others let it stream.

# The services with a Dockerfile. Each builds independently, so the order is arbitrary.
SERVICES="postgresql crudman sqlmesh proxy grafana"

# Render the templated parts of the two images whose Dockerfiles COPY them in. Grafana gets
# APP_NAME and SERVER_STATS_SCHEMA baked into its dashboard JSON, which Grafana itself
# cannot interpolate; PostgreSQL gets the database role names baked into its init scripts,
# which psql cannot interpolate inside a function body. Without this the COPY of
# grafana/.render/ and postgresql/.initdb/ has no source. The output is deterministic, so an
# unchanged dashboard keeps the COPY layer cached.
render_build_templates() {
  ./grafana/render.sh grafana/.render
  ./postgresql/render.sh postgresql/.initdb
}

# Build one service image, tagged REGISTRY/<svc>:IMAGE_TAG to match the Image= lines in the
# quadlets. The proxy settings are passed only to docker: podman copies them from its own
# environment, where the caller's `set -a` put them, while docker does not.
build_image() {  # engine, service
  engine="$1"
  svc="$2"
  set -- \
    --build-arg "PYTHON_INDEX=${PYTHON_INDEX}" \
    --build-arg "DOCKER_IO_MIRROR=${DOCKER_IO_MIRROR}" \
    --build-arg "GHCR_IO_MIRROR=${GHCR_IO_MIRROR}" \
    --build-arg "SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}" \
    --build-arg "SECRET_SUPERUSER_PASSWORD=${SECRET_SUPERUSER_PASSWORD}" \
    --build-arg "DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}" \
    --build-arg "GRAFANA_PLUGINS=${GRAFANA_PLUGINS}"
  if [ "$engine" = "docker" ]; then
    set -- "$@" \
      --build-arg "http_proxy=${HTTP_PROXY}" \
      --build-arg "https_proxy=${HTTPS_PROXY}" \
      --build-arg "no_proxy=${NO_PROXY}"
  fi
  "$engine" build "$@" -t "${REGISTRY}/${svc}:${IMAGE_TAG}" -f "${svc}/Dockerfile" .
}
