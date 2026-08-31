#!/bin/sh
set -e

# Grafana builds the sign-in callback from root_url rather than from the request, and
# root_url's %(protocol)s is what Grafana serves inside the pod -- http, the proxy
# terminating TLS. A production system is reached over https, so the provider would be
# handed a callback it never registered. Settled here from DEBUG, a quadlet not being able
# to branch on a runtime setting.
if [ -z "${GF_SERVER_ROOT_URL:-}" ]; then
  # As the proxy entrypoint and the Django settings do: runtime.env saved with CRLF
  # endings yields "true\r", which matches neither branch.
  debug="$(printf '%s' "${DEBUG:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  if [ "$debug" = "true" ]; then
    scheme=http
  else
    scheme=https
  fi
  export GF_SERVER_ROOT_URL="${scheme}://${SERVER_NAME:-localhost}/${GRAFANA_PATH}/"
fi

# An address set from outside wins, and none is derived with a port: a container cannot
# see the port it is published on. The line the README gives a custom-port install.
echo "Grafana serves itself as ${GF_SERVER_ROOT_URL}" >&2

# The image's own entrypoint, which builds and runs grafana-server's command line.
exec /run.sh "$@"
