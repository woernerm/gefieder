#!/bin/sh
# Render the database init scripts into a throwaway directory that the Dockerfile then
# COPYs into the image. build.sh calls this before `docker build`.
#
#   ./postgresql/render.sh <output-dir>
#
# Why a render step instead of letting the scripts read the values at runtime: the role
# names appear inside plpgsql function bodies (the event triggers in gf_0005 and gf_0008,
# the SECURITY DEFINER functions in gf_0003), and psql interpolates neither a variable nor
# an environment value inside a dollar-quoted block. Baking them in here is the only place
# every occurrence can be reached with one mechanism -- the same reason grafana/render.sh
# exists for the dashboard JSON. The values come from buildtime.env, already sourced into
# the environment by whichever build script called this.
#
# Only the names listed below are substituted; every other $-token is passed through
# untouched -- the shell variables the .sh scripts use ($POSTGRES_USER, ${user}), psql's
# :'var' references and SQL's own $$ ... $$ quoting all survive, the same explicit-allowlist
# approach build.sh and run-tests.sh use for the quadlets.
set -e

out="${1:?usage: render.sh <output-dir>}"
src="$(dirname "$0")/initdb"

# The role names the scripts create and grant on, the database gf_0008 grants CREATE on, and
# the medallion schemas they create and the event triggers match against. SERVER_STATS_SCHEMA is not among them: gf_0007 reads it
# as a shell variable from the image's ENV, so it stays adjustable without a rebuild of the
# scripts themselves.
VARS='${PG_DATABASE} ${CRUDMAN_DB_USER} ${SQLMESH_DB_USER} ${GRAFANA_DB_USER} ${DB_ROLE_PREFIX} ${BRONZE_SCHEMA_PREFIX} ${SILVER_SCHEMA} ${GOLD_SCHEMA} ${SECRET_CRUDMAN_PASSWORD} ${SECRET_SQLMESH_PASSWORD} ${SECRET_GRAFANA_PASSWORD}'
: "${PG_DATABASE:?PG_DATABASE must be set (source buildtime.env first)}"
: "${CRUDMAN_DB_USER:?CRUDMAN_DB_USER must be set (source buildtime.env first)}"
: "${SQLMESH_DB_USER:?SQLMESH_DB_USER must be set (source buildtime.env first)}"
: "${GRAFANA_DB_USER:?GRAFANA_DB_USER must be set (source buildtime.env first)}"
: "${DB_ROLE_PREFIX:?DB_ROLE_PREFIX must be set (source buildtime.env first)}"
: "${BRONZE_SCHEMA_PREFIX:?BRONZE_SCHEMA_PREFIX must be set (source buildtime.env first)}"
: "${SILVER_SCHEMA:?SILVER_SCHEMA must be set (source buildtime.env first)}"
: "${GOLD_SCHEMA:?GOLD_SCHEMA must be set (source buildtime.env first)}"
: "${SECRET_CRUDMAN_PASSWORD:?SECRET_CRUDMAN_PASSWORD must be set (source buildtime.env first)}"
: "${SECRET_SQLMESH_PASSWORD:?SECRET_SQLMESH_PASSWORD must be set (source buildtime.env first)}"
: "${SECRET_GRAFANA_PASSWORD:?SECRET_GRAFANA_PASSWORD must be set (source buildtime.env first)}"

rm -rf "$out"
mkdir -p "$out"
# The PostgreSQL entrypoint runs the scripts in filename order, so the names have to
# survive the rendering unchanged.
for f in "$src"/*; do
  envsubst "$VARS" < "$f" > "$out/$(basename "$f")"
done
