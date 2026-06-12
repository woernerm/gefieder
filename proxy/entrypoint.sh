#!/bin/sh
set -e

# Select the proxy configuration: plain HTTP for development (DEBUG=true), HTTPS with
# an HTTP-to-HTTPS redirect for production. The certificate files are expected in
# proxy/certs/, see the Readme.
if [ "$DEBUG" = "true" ]; then
  cp /etc/nginx/proxy/http.conf /etc/nginx/conf.d/default.conf
else
  cp /etc/nginx/proxy/https.conf /etc/nginx/conf.d/default.conf
fi

exec nginx -g 'daemon off;'
