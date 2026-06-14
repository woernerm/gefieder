#!/bin/sh
set -e

# The base path under which the administration panel is served. It must match
# CRUDMAN_PATH of the crudman service.
export CRUDMAN_PATH="${CRUDMAN_PATH:-crudman}"

# Select the proxy configuration: plain HTTP for development (DEBUG=true), HTTPS with
# an HTTP-to-HTTPS redirect for production. The certificate files are expected in
# proxy/certs/, see the Readme.
if [ "$DEBUG" = "true" ]; then
  template=/etc/nginx/proxy/http.conf.template
else
  template=/etc/nginx/proxy/https.conf.template
fi

# Render the chosen template, substituting only ${CRUDMAN_PATH} so that nginx's own
# variables ($host, $scheme, ...) are left untouched.
envsubst '${CRUDMAN_PATH}' < "$template" > /etc/nginx/conf.d/default.conf

exec nginx -g 'daemon off;'
