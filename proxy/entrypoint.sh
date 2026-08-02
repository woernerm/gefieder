#!/bin/sh
set -e

# This script and nginx log to stdout/stderr only; journald captures the stream and is
# what survives a crash, a container replacement and a restart. It rotates and size-caps
# the log on its own, which a file on the volume did not. The nginx image already sends
# its access/error logs to stdout/stderr, so they are covered too.
#
# The volume is still mounted at $LOG_DIR: nginx writes visits.log there (see the
# access_log directives in the conf templates), which the server-statistics collector
# drains by byte offset. That file is data for the collector, not a human log.
LOG_DIR=/var/log/app
mkdir -p "$LOG_DIR"

# The base paths under which the administration panel and Grafana are served. They
# must match CRUDMAN_PATH of the crudman service and GRAFANA_PATH of the grafana
# service respectively.
export CRUDMAN_PATH="${CRUDMAN_PATH:-crudman}"
export GRAFANA_PATH="${GRAFANA_PATH:-grafana}"

# Select the proxy configuration: plain HTTP for development (DEBUG=true), HTTPS with
# an HTTP-to-HTTPS redirect for production. The certificate files are expected in
# proxy/certs/, see the README.
#
# DEBUG comes from runtime.env, which the operator edits by hand. Strip whitespace and
# fold the case before comparing, the way the Django settings do: a file saved with CRLF
# line endings yields "true\r", which matches neither branch cleanly and would otherwise
# serve HTTPS while Django serves its debug pages -- a split state that is hard to spot.
debug="$(printf '%s' "${DEBUG:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
if [ "$debug" = "true" ]; then
  template=/etc/nginx/proxy/http.conf.template
else
  template=/etc/nginx/proxy/https.conf.template
  # Check the certificate ourselves so a missing one is stated plainly. nginx would other-
  # wise fail with a path inside the container, which says nothing about where the operator
  # has to put the files. CERTIFICATE_HINT is that host directory, passed in by the quadlet
  # because only systemd can resolve it; without it, name the mount point.
  for f in fullchain.pem privkey.pem; do
    if [ ! -f "/etc/nginx/proxy/certs/$f" ]; then
      echo "TLS certificate missing: $f was not found in" >&2
      echo "  ${CERTIFICATE_HINT:-/etc/nginx/proxy/certs (inside the container)}" >&2
      echo "Production mode (DEBUG=false) serves HTTPS and needs both fullchain.pem and" >&2
      echo "privkey.pem there. Put them in place, then:" >&2
      echo "  systemctl --user restart proxy.service" >&2
      exit 1
    fi
  done
fi

# Render the chosen template, substituting only our own variables so that nginx's
# variables ($host, $scheme, ...) are left untouched.
envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}' < "$template" > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
