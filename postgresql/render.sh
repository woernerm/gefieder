#!/bin/sh
# Render the database init scripts into a throwaway directory that the Dockerfile then
# COPYs into the image. build.sh calls this before `docker build`.
#
#   ./postgresql/render.sh <output-dir>
#
# A render step rather than runtime interpolation because the role names appear inside
# plpgsql function bodies, where psql interpolates neither a variable nor an environment
# value -- the same reason grafana/render.sh exists.
#
# The substitution is render_tree in build-lib.sh. Only the names listed below are
# substituted; every other $-token survives: the shell variables the .sh scripts use,
# psql's :'var' references and SQL's own $$ ... $$ quoting.
set -e

out="${1:?usage: render.sh <output-dir>}"
here="$(dirname "$0")"

. "$here/../build-lib.sh"

# The role names the scripts create and grant on, the database gf_0008 grants CREATE on,
# and the medallion schemas. DB_USER_PREFIX is absent because a personal account is
# recognised by the marker is_db_user tests, never by its name; SERVER_STATS_SCHEMA because
# gf_0007 reads it from the image's ENV, staying adjustable without a rebuild.
VARS='${PG_DATABASE} ${CRUDMAN_DB_USER} ${SQLMESH_DB_USER} ${GRAFANA_DB_USER} ${ROLE_PREFIX} ${BRONZE_SCHEMA_PREFIX} ${SILVER_SCHEMA} ${GOLD_SCHEMA} ${SECRET_CRUDMAN_PASSWORD} ${SECRET_SQLMESH_PASSWORD} ${SECRET_GRAFANA_PASSWORD}'

# The entrypoint runs the scripts in filename order, so render_tree mirrors the tree.
render_tree "$VARS" "$out" "$here/initdb"
