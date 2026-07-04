#!/bin/sh
# Render the Grafana provisioning templates into a throwaway directory that the Dockerfile
# then COPYs into the image. build.sh calls this before `docker build`.
#
#   ./grafana/render.sh <output-dir>
#
# Why a render step instead of letting Grafana interpolate at runtime: Grafana only expands
# ${VAR} inside provisioning YAML, never inside the dashboard JSON. Baking the values in here
# lets the dashboards reference the real data-source uid and the configured server-stats
# schema, so a renamed schema keeps working and no per-dashboard datasource variable is
# needed. The values come from buildtime.env, already sourced into the environment by build.sh.
#
# Only the names listed below are substituted; every other $-token (notably the $__file{}
# secret reference in the datasource and Grafana's own %(...)s tokens) is passed through
# untouched, the same explicit-allowlist approach run-tests.sh uses for the quadlets.
set -e

out="${1:?usage: render.sh <output-dir>}"
src="$(dirname "$0")/provisioning"

# The only variables the templates reference. APP_NAME names the data source (and its uid),
# SERVER_STATS_SCHEMA is the schema the dashboard SQL reads from.
VARS='${APP_NAME} ${SERVER_STATS_SCHEMA}'
: "${APP_NAME:?APP_NAME must be set (source buildtime.env first)}"
: "${SERVER_STATS_SCHEMA:?SERVER_STATS_SCHEMA must be set (source buildtime.env first)}"

rm -rf "$out"
# Recreate the source tree, then substitute in place so the directory layout Grafana expects
# (datasources/, dashboards/) is preserved regardless of nesting.
find "$src" -type d | while read -r d; do
  mkdir -p "$out/${d#"$src"}"
done
# Skip Markdown docs (the Readme.md explaining this folder): it is for the repository, not the
# image, and its prose shows the ${...} template syntax literally, which envsubst must not expand.
find "$src" -type f ! -name '*.md' | while read -r f; do
  envsubst "$VARS" < "$f" > "$out/${f#"$src"}"
done
