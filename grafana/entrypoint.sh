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

# The assistant dashboard prints the MCP address for people to copy, and that address is
# only known now: it carries SERVER_NAME and DEBUG, both runtime settings, while the
# dashboard JSON is baked into the image. So @@MCP_URL@@ is substituted here, derived from
# the root URL settled above -- which means an operator who overrides GF_SERVER_ROOT_URL for
# a custom port gets the matching MCP address for free.
#
# Into a copy under /var/lib/grafana, the provisioning directory being root-owned while the
# server runs as grafana. The dashboards provider is pointed at both directories.
mcp_dashboards=/var/lib/grafana/dashboards-runtime
rm -rf "$mcp_dashboards"
mkdir -p "$mcp_dashboards"
mcp_url="${GF_SERVER_ROOT_URL%/}"
# Swap Grafana's own subpath for the MCP server's; both are build-time settings, so the
# result is the address the proxy actually routes.
mcp_url="${mcp_url%/${GRAFANA_PATH}}/${MCP_PATH:-ai/grafana_mcp}/mcp"
for dashboard in /etc/grafana/provisioning/dashboards-runtime/*.json; do
  [ -e "$dashboard" ] || continue
  # A URL contains slashes, so "|" delimits the expression.
  sed "s|@@MCP_URL@@|${mcp_url}|g" "$dashboard" > "$mcp_dashboards/$(basename "$dashboard")"
done

# The image's own entrypoint, which builds and runs grafana-server's command line.
exec /run.sh "$@"
