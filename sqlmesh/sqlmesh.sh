#!/bin/sh
# SQLMesh against the deployed project, from anywhere in this container:
#
#   podman exec sqlmesh sqlmesh plan dev
#   podman exec sqlmesh sqlmesh test
#
# Installed as /usr/local/bin/sqlmesh, ahead of the one in the virtual environment. Three
# things have to be settled before the CLI is useful here and none of them can be guessed
# from the command line:
#
#   -p            the project is a checkout on the models volume, not a directory in this
#                 image, and it moves when a different version is deployed
#   --log-file-dir  SQLMesh creates "logs" beside the working directory whatever else it is
#                 told, and that checkout is mounted read-only
#   --log-to-stdout  the handler its logger otherwise lacks, so journald sees the run
#
# The entrypoint uses it too, so there is one spelling of "run SQLMesh here".
MODELS_DIR="${MODELS_DIR:-/var/lib/app/models}"
exec uv run --project /sqlmesh sqlmesh \
  --log-to-stdout --log-file-dir /tmp/sqlmesh-logs \
  --paths "${MODELS_DIR}/deployed/sqlmesh" "$@"
