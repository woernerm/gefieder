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

# nginx takes the first certificate in fullchain.pem as the server certificate and checks
# it against privkey.pem; the rest it forwards to clients as the trust chain. A file
# assembled by hand -- e.g. converted from a p7b a CA handed out, which some tools emit
# root-first -- may not have the leaf in that position, and nginx then refuses to start
# with "key values mismatch" rather than a message naming the file. This puts the leaf
# first regardless of the order the operator's file has them in, so that order stops
# mattering. It writes the result to certs-effective/ instead of editing the operator's
# file, which CERTIFICATE_PATH bind-mounts read-only anyway (see https.conf.template).
reorder_fullchain() {
  certs_dir=/etc/nginx/proxy/certs
  out_dir=/etc/nginx/proxy/certs-effective
  mkdir -p "$out_dir"
  split_dir="$(mktemp -d)"

  # One file per certificate in the bundle, numbered in the order they appear, held in
  # the positional parameters throughout this function ("$original" is a snapshot for
  # the before/after comparison at the end).
  #
  # "file" is cleared at END CERTIFICATE, not merely closed: awk truncates a file when a
  # "print >" reopens it after a close(), so leaving the name set would empty the finished
  # certificate again on the very next line. Anything between two certificates -- the
  # blank lines and subject=/issuer= headers that "openssl pkcs7 -print_certs" writes when
  # a p7b is converted -- is skipped for the same reason.
  awk -v dir="$split_dir" '
    /-----BEGIN CERTIFICATE-----/ { n++; file = sprintf("%s/%04d.pem", dir, n) }
    file { print > file }
    /-----END CERTIFICATE-----/ { close(file); file = "" }
  ' "$certs_dir/fullchain.pem"
  set -- "$split_dir"/*.pem
  [ -e "$1" ] || set --
  original="$*"

  # The certificate whose public key matches the private key is the leaf, wherever it
  # sits in the file: nginx pairs privkey.pem against the certificate in front, not
  # against any particular position.
  key_pubkey="$(openssl pkey -in "$certs_dir/privkey.pem" -pubout 2>/dev/null | openssl dgst -sha256)"
  leaf=""
  for f in "$@"; do
    if [ -z "$leaf" ] && [ "$(openssl x509 -in "$f" -noout -pubkey 2>/dev/null | openssl dgst -sha256)" = "$key_pubkey" ]; then
      leaf="$f"
    fi
  done
  if [ -z "$leaf" ]; then
    # Either the bundle held no certificate, or none of them match the key: leave the
    # order as found and let nginx report the problem itself, exactly as it did before
    # this function existed.
    [ -z "$original" ] && echo "TLS certificate: fullchain.pem contains no certificate" >&2
    cp "$certs_dir/fullchain.pem" "$out_dir/fullchain.pem"
    rm -rf "$split_dir"
    return
  fi

  # Walk from the leaf up the chain, each next certificate being the one whose subject
  # is the current one's issuer, until a self-signed root is reached or the issuer is
  # not among the remaining certificates. This orders the chain correctly no matter how
  # the operator's file had it, including a shuffled set of intermediates. Anything left
  # over (unrelated or duplicate certificates) is appended unchanged at the end.
  ordered="$leaf"
  set -- $(printf '%s\n' "$@" | grep -vxF "$leaf")
  current="$leaf"
  while [ "$#" -gt 0 ]; do
    issuer="$(openssl x509 -in "$current" -noout -issuer)"
    [ "$issuer" = "$(openssl x509 -in "$current" -noout -subject)" ] && break
    next=""
    for f in "$@"; do
      if [ -z "$next" ] && [ "$(openssl x509 -in "$f" -noout -subject)" = "$issuer" ]; then
        next="$f"
      fi
    done
    [ -z "$next" ] && break
    ordered="$ordered $next"
    set -- $(printf '%s\n' "$@" | grep -vxF "$next")
    current="$next"
  done
  [ "$#" -gt 0 ] && ordered="$ordered $*"

  cat $ordered > "$out_dir/fullchain.pem"
  if [ "$ordered" != "$original" ]; then
    echo "TLS certificate: fullchain.pem was not in leaf-first order; reordered it" >&2
    echo "  for nginx (the file on disk was left untouched)." >&2
  fi
  rm -rf "$split_dir"
}

# Select the proxy configuration: plain HTTP for development (DEBUG=true), HTTPS with
# an HTTP-to-HTTPS redirect for production. The certificate files are bind-mounted from
# CERTIFICATE_PATH on the host, see the README.
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
  reorder_fullchain
fi

# Render the chosen template, substituting only our own variables so that nginx's
# variables ($host, $scheme, ...) are left untouched.
envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}' < "$template" > /etc/nginx/conf.d/default.conf

# The templates listen on IPv6 as well, so clients whose DNS answers with an AAAA record are
# served. A kernel built or booted without IPv6 has no such address family, and nginx aborts
# at startup rather than skipping the directive -- which would take the whole system down on
# a host that was serving IPv4 clients perfectly well. Drop those lines there instead; this
# is the check the nginx image makes for the configuration it ships itself.
if [ ! -f /proc/net/if_inet6 ]; then
  sed -i '/listen \[::\]/d' /etc/nginx/conf.d/default.conf
  echo "IPv6 is not available on this host; serving IPv4 only" >&2
fi

exec nginx -g 'daemon off;'
