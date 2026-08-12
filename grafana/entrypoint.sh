#!/bin/sh
set -e

# Grafana builds the sign-in callback it hands the identity provider from root_url rather
# than from the request, and root_url's %(protocol)s is the protocol Grafana serves inside
# the pod -- http, and staying http, because the proxy terminates TLS. A production system
# is reached over https, so the provider would be handed a callback it never registered.
# Hence the scheme is settled here, from DEBUG, as the proxy picks its template: a quadlet
# cannot branch on a runtime setting.
if [ -z "${GF_SERVER_ROOT_URL:-}" ]; then
  # Strip whitespace and fold the case as the proxy entrypoint and the Django settings do:
  # runtime.env saved with CRLF endings yields "true\r", which matches neither branch.
  debug="$(printf '%s' "${DEBUG:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  if [ "$debug" = "true" ]; then
    scheme=http
  else
    scheme=https
  fi
  export GF_SERVER_ROOT_URL="${scheme}://${SERVER_NAME:-localhost}/${GRAFANA_PATH}/"
fi

# An address set from outside wins, and none is derived with a port: a container cannot see
# the port it is published on. That is the line the README asks a custom-port install to add.
echo "Grafana serves itself as ${GF_SERVER_ROOT_URL}" >&2

# The image's own entrypoint, which builds grafana-server's command line and runs it.
exec /run.sh "$@"
