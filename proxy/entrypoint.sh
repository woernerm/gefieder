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
if [ "$DEBUG" = "true" ]; then
  template=/etc/nginx/proxy/http.conf.template
else
  template=/etc/nginx/proxy/https.conf.template
fi

# Render the chosen template, substituting only our own variables so that nginx's
# variables ($host, $scheme, ...) are left untouched.
envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}' < "$template" > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
