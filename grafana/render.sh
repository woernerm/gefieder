#!/bin/sh
# Render the Grafana configuration and provisioning templates into a throwaway directory
# that the Dockerfile then COPYs into the image. build.sh calls this before `docker build`.
#
#   ./grafana/render.sh <output-dir>
#
# It writes <output-dir>/provisioning/ (the data source and the shipped dashboards) and
# <output-dir>/grafana.ini (the server configuration, from custom.ini).
#
# Why a render step instead of letting Grafana interpolate at runtime: Grafana only expands
# ${VAR} inside provisioning YAML, never inside the dashboard JSON and never in its own
# configuration file. Baking the values in here lets the dashboards reference the real
# data-source uid and the configured server-stats schema, and lets both files name the
# podman secrets they read passwords from, so a renamed schema or secret keeps working. The
# values come from buildtime.env, already sourced into the environment by build.sh.
#
# Only the names listed below are substituted; every other $-token (notably the $__file{}
# and $__env{} references Grafana resolves itself, and its own %(...)s tokens) is passed
# through untouched, the same explicit-allowlist approach run-tests.sh uses for the quadlets.
set -e

out="${1:?usage: render.sh <output-dir>}"
here="$(dirname "$0")"
src="$here/provisioning"

# The only variables the templates reference. APP_NAME names the data source (and its uid),
# SERVER_STATS_SCHEMA is the schema the dashboard SQL reads from, and GRAFANA_DB_USER is
# the login role the data source connects as -- the one postgresql/render.sh baked into the
# database init scripts, so both sides of that connection come from the same setting.
# PG_DATABASE is the database it opens there, for the same reason. The
# two SECRET_* names are the files the data-source password and the single sign-on client
# secret are read from at runtime; only the name is substituted, the $__file{} around it is
# Grafana's to resolve.
VARS='${APP_NAME} ${SERVER_STATS_SCHEMA} ${PG_DATABASE} ${GRAFANA_DB_USER} ${SECRET_GRAFANA_PASSWORD} ${SECRET_OIDC_CLIENT}'
: "${APP_NAME:?APP_NAME must be set (source buildtime.env first)}"
: "${SERVER_STATS_SCHEMA:?SERVER_STATS_SCHEMA must be set (source buildtime.env first)}"
: "${PG_DATABASE:?PG_DATABASE must be set (source buildtime.env first)}"
: "${GRAFANA_DB_USER:?GRAFANA_DB_USER must be set (source buildtime.env first)}"
: "${SECRET_GRAFANA_PASSWORD:?SECRET_GRAFANA_PASSWORD must be set (source buildtime.env first)}"
: "${SECRET_OIDC_CLIENT:?SECRET_OIDC_CLIENT must be set (source buildtime.env first)}"

rm -rf "$out"
mkdir -p "$out"
# Recreate the source tree, then substitute in place so the directory layout Grafana expects
# (datasources/, dashboards/) is preserved regardless of nesting.
find "$src" -type d | while read -r d; do
  mkdir -p "$out/provisioning/${d#"$src"}"
done
# Skip Markdown docs (the Readme.md explaining this folder): it is for the repository, not the
# image, and its prose shows the ${...} template syntax literally, which envsubst must not expand.
find "$src" -type f ! -name '*.md' | while read -r f; do
  envsubst "$VARS" < "$f" > "$out/provisioning/${f#"$src"}"
done

envsubst "$VARS" < "$here/custom.ini" > "$out/grafana.ini"
