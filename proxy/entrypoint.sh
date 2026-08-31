#!/bin/sh
set -e

# This script and nginx log to stdout/stderr only, and journald rotates and size-caps that
# stream, which a file on the volume did not.
#
# The volume is still mounted at $LOG_DIR: nginx writes visits.log there for the
# server-statistics collector to drain by byte offset. That file is data, not a log.
LOG_DIR=/var/log/app
mkdir -p "$LOG_DIR"

# Must match CRUDMAN_PATH and GRAFANA_PATH of the two services.
export CRUDMAN_PATH="${CRUDMAN_PATH:-crudman}"
export GRAFANA_PATH="${GRAFANA_PATH:-grafana}"

# nginx takes the first certificate in fullchain.pem as the server certificate and checks
# it against privkey.pem, forwarding the rest as the trust chain. A file converted from a
# CA's p7b may be root-first, and nginx then refuses to start with "key values mismatch".
# This puts the leaf first whatever order the operator's file has, writing the result to
# certs-effective/ rather than editing that file, which is mounted read-only.
reorder_fullchain() {
  certs_dir=/etc/nginx/proxy/certs
  out_dir=/etc/nginx/proxy/certs-effective
  mkdir -p "$out_dir"
  split_dir="$(mktemp -d)"

  # One file per certificate, numbered in the order they appear and held in the positional
  # parameters throughout; "$original" is a snapshot for the comparison at the end.
  #
  # "file" is cleared at END CERTIFICATE, not merely closed: awk truncates a file when a
  # "print >" reopens it after close(), so a name left set would empty the finished
  # certificate on the next line. Anything between two certificates is skipped likewise.
  awk -v dir="$split_dir" '
    /-----BEGIN CERTIFICATE-----/ { n++; file = sprintf("%s/%04d.pem", dir, n) }
    file { print > file }
    /-----END CERTIFICATE-----/ { close(file); file = "" }
  ' "$certs_dir/fullchain.pem"
  set -- "$split_dir"/*.pem
  [ -e "$1" ] || set --
  original="$*"

  # The certificate whose public key matches the private key is the leaf, wherever it
  # sits in the file.
  key_pubkey="$(openssl pkey -in "$certs_dir/privkey.pem" -pubout 2>/dev/null | openssl dgst -sha256)"
  leaf=""
  for f in "$@"; do
    if [ -z "$leaf" ] && [ "$(openssl x509 -in "$f" -noout -pubkey 2>/dev/null | openssl dgst -sha256)" = "$key_pubkey" ]; then
      leaf="$f"
    fi
  done
  if [ -z "$leaf" ]; then
    # No certificate, or none matching the key: leave the order as found and let nginx
    # report the problem itself.
    [ -z "$original" ] && echo "TLS certificate: fullchain.pem contains no certificate" >&2
    cp "$certs_dir/fullchain.pem" "$out_dir/fullchain.pem"
    rm -rf "$split_dir"
    return
  fi

  # From the leaf up, each next certificate being the one whose subject is the current
  # one's issuer, until a self-signed root or an issuer not among the rest. Anything left
  # over -- unrelated or duplicate certificates -- is appended unchanged.
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

# Plain HTTP for development, HTTPS with a redirect for production. The certificate files
# are bind-mounted from CERTIFICATE_PATH on the host; see the README.
#
# DEBUG is edited by hand in runtime.env, so whitespace is stripped and the case folded as
# the Django settings do: a file saved with CRLF yields "true\r", which matches neither
# branch and would leave the proxy on HTTPS while Django serves its debug pages.
debug="$(printf '%s' "${DEBUG:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
if [ "$debug" = "true" ]; then
  template=/etc/nginx/proxy/http.conf.template
else
  template=/etc/nginx/proxy/https.conf.template
  # nginx would fail with a path inside the container, which says nothing about where the
  # files go. CERTIFICATE_HINT is that host directory, passed in by the quadlet because
  # only systemd can resolve it.
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

# Only our own variables, so nginx's ($host, $scheme, ...) survive. The fragments are
# rendered beside the templates, where the include lines name them.
envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}' < "$template" > /etc/nginx/conf.d/default.conf
for fragment in maps locations; do
  envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}' \
    < "/etc/nginx/proxy/${fragment}.conf.template" > "/etc/nginx/proxy/${fragment}.conf"
done

# The templates listen on IPv6 too, so a client whose DNS answers with an AAAA record is
# served. A kernel without IPv6 has no such address family, and nginx aborts at startup
# rather than skipping the directive, so those lines are dropped there -- the check the
# nginx image makes for its own configuration.
if [ ! -f /proc/net/if_inet6 ]; then
  sed -i '/listen \[::\]/d' /etc/nginx/conf.d/default.conf
  echo "IPv6 is not available on this host; serving IPv4 only" >&2
fi

exec nginx -g 'daemon off;'
