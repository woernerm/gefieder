#!/bin/sh
# Render the database init scripts into a throwaway directory that the Dockerfile then
# COPYs into the image. build.sh calls this before `docker build`.
#
#   ./postgresql/render.sh <output-dir>
#
# A render step rather than runtime interpolation because the role names appear inside
# plpgsql function bodies, and psql interpolates neither a variable nor an environment
# value inside a dollar-quoted block. Baking them in is the only mechanism that reaches
# every occurrence — the same reason grafana/render.sh exists. The values come from
# buildtime.env, already sourced by whichever build script called this.
#
# The substitution is render_tree in build-lib.sh, shared with grafana/render.sh. Only the
# names listed below are substituted; every other $-token survives — the shell variables
# the .sh scripts use, psql's :'var' references and SQL's own $$ ... $$ quoting.
set -e

out="${1:?usage: render.sh <output-dir>}"
here="$(dirname "$0")"

. "$here/../build-lib.sh"

# The role names the scripts create and grant on, the database gf_0008 grants CREATE on,
# and the medallion schemas they create and the event triggers match against.
# DB_USER_PREFIX is not among them: a personal account is recognised by the marker role
# is_db_user tests, never by its name, so the scripts never spell the prefix out.
# SERVER_STATS_SCHEMA is not among them: gf_0007 reads it as a shell variable from the
# image's ENV, so it stays adjustable without a rebuild of the scripts themselves.
VARS='${PG_DATABASE} ${CRUDMAN_DB_USER} ${SQLMESH_DB_USER} ${GRAFANA_DB_USER} ${ROLE_PREFIX} ${BRONZE_SCHEMA_PREFIX} ${SILVER_SCHEMA} ${GOLD_SCHEMA} ${SECRET_CRUDMAN_PASSWORD} ${SECRET_SQLMESH_PASSWORD} ${SECRET_GRAFANA_PASSWORD}'

# The PostgreSQL entrypoint runs the scripts in filename order, so the names have to
# survive the rendering unchanged -- which render_tree does, mirroring the source tree.
render_tree "$VARS" "$out" "$here/initdb"
