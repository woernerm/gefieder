#!/bin/sh
# Render the Grafana configuration and provisioning templates into a throwaway directory
# that the Dockerfile then COPYs into the image. build.sh calls this before `docker build`.
#
#   ./grafana/render.sh <output-dir>
#
# It writes <output-dir>/provisioning/ (the data source and the shipped dashboards) and
# <output-dir>/grafana.ini (the server configuration, from custom.ini).
#
# A render step rather than runtime interpolation because Grafana expands ${VAR} only
# inside provisioning YAML, never in the dashboard JSON or its own configuration file.
# Baking the values in lets the dashboards name the real data-source uid, the configured
# schema and the podman secrets, so a rename keeps working.
#
# The substitution is render_tree in build-lib.sh. Only the names listed below are
# substituted; every other $-token survives, notably Grafana's own $__file{}, $__env{} and
# %(...)s.
set -e

out="${1:?usage: render.sh <output-dir>}"
here="$(dirname "$0")"

. "$here/../build-lib.sh"

# The only variables the templates reference. APP_NAME names the data source and its uid;
# SERVER_STATS_SCHEMA, GRAFANA_DB_USER and PG_DATABASE are what the dashboard SQL reads and
# what the data source connects as, the same settings postgresql/render.sh baked into the
# init scripts. The two SECRET_* names are the files the passwords are read from at
# runtime; only the name is substituted, the $__file{} around it being Grafana's.
# GRAFANA_PATH and MCP_PATH are the two paths the AI-assistant dashboard spells out.
VARS='${APP_NAME} ${SERVER_STATS_SCHEMA} ${PG_DATABASE} ${GRAFANA_DB_USER} ${SECRET_GRAFANA_PASSWORD} ${SECRET_OIDC_CLIENT} ${GRAFANA_PATH} ${MCP_PATH}'

# The Readme.md explaining this folder is for the repository, not the image, and its prose
# shows the ${...} syntax literally.
render_tree "$VARS" "$out/provisioning" "$here/provisioning" ! -name '*.md'
render_tree "$VARS" "$out/grafana.ini" "$here/custom.ini"
