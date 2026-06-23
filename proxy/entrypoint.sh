#!/bin/sh
set -e

# Persist everything this script and nginx print into the mounted log volume while still
# echoing to stdout, so "podman logs"/journald keep working and a crash also leaves its
# cause on disk. Process substitution is a bashism unavailable in this BusyBox /bin/sh,
# so on first entry the script re-runs itself with stdout+stderr piped through tee -a
# (which appends, so logs survive restarts); GEFIEDER_LOGGING guards against looping. A
# pipeline cannot be exec'd and its status would be tee's, so the real exit status is
# captured via a status file and re-raised, keeping the container's exit code (and thus
# Restart=) honest on a crash. The nginx image already sends its access/error logs to
# stdout/stderr, so this captures them too. The volume is owned by the podman user.
LOG_DIR=/var/log/gefieder
if [ -z "$GEFIEDER_LOGGING" ]; then
  mkdir -p "$LOG_DIR"
  export GEFIEDER_LOGGING=1
  STATUS_FILE="$(mktemp)"
  { "$0" "$@"; echo $? > "$STATUS_FILE"; } 2>&1 | tee -a "$LOG_DIR/proxy.log" || true
  status="$(cat "$STATUS_FILE" 2>/dev/null || echo 1)"; rm -f "$STATUS_FILE"
  exit "$status"
fi

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
