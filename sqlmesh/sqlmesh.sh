#!/bin/sh
# SQLMesh against the deployed project, from anywhere in this container:
#
#   podman exec sqlmesh sqlmesh plan dev
#   podman exec sqlmesh sqlmesh test
#
# Installed as /usr/local/bin/sqlmesh, ahead of the one in the virtual environment, and used
# by the entrypoint too. It settles:
#
#   -p               the project, a checkout on the models volume that moves per deployment
#   --log-file-dir   as SQLMesh creates "logs" beside the read-only checkout otherwise
#   --log-to-stdout  the handler its logger otherwise lacks, so journald sees the run
#
# SQLMESH_TREE names the checkout: "deployed" for production, or a version under review.
MODELS_DIR="${MODELS_DIR:-/var/lib/app/models}"
exec uv run --project /sqlmesh sqlmesh \
  --log-to-stdout --log-file-dir /tmp/sqlmesh-logs \
  --paths "${MODELS_DIR}/${SQLMESH_TREE:-deployed}/sqlmesh" "$@"
