#!/bin/sh
set -e

# streamable-http rather than the image's default sse: it is the transport current MCP
# clients speak, and the one --server-auth-token and header forwarding are defined for.
#
# 8001, not the image's own 8000: every container shares the pod's network namespace and
# crudman's gunicorn already listens there.
#
# --allowed-hosts=*: the check exists against DNS rebinding by a browser, and no browser
# speaks MCP. The proxy passes the browser's Host through unchanged, so the container would
# have to be told every name the system is reached under -- SERVER_NAME, localhost, and the
# host's own address. The proxy is the only route in; the pod publishes no port for this.
exec /app/mcp-grafana \
  -t streamable-http \
  --address "0.0.0.0:8001" \
  --allowed-hosts "*" \
  --enabled-tools "${GRAFANA_MCP_TOOLS}" \
  "$@"
