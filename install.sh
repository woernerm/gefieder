#!/bin/sh
# Installer for a GitHub release.
#
# Run it straight from a release without a checkout:
#
#   curl -fsSL ${REPO}/releases/latest/download/install.sh | bash
#
# It downloads each release asset, loads the image tarballs into rootless podman,
# installs the rendered quadlets, creates the machine secrets and prints a cheat sheet.
# What was baked into the images at build time is recorded in the release's manifest.env.
set -e

# --- where the release lives ----------------------------------------------------------
# Baked in from buildtime.env when the release is built, so an enterprise GitHub instance
# works as well as github.com. No default: guessing would pull someone else's release.
# From a checkout, set it (and TAG, to pin a version):
#   REPO=https://github.example.com/myorg/myrepo TAG=v1.2.0 ./install.sh
REPO="${REPO}"
if [ -z "$REPO" ]; then
  echo "REPO is required: the full URL of the repository holding the release, e.g." >&2
  echo "  REPO=https://github.example.com/myorg/myrepo ./install.sh" >&2
  exit 1
fi

TAG="${TAG:-latest}"
if [ "$TAG" = "latest" ]; then
  BASE="${REPO}/releases/latest/download"
else
  BASE="${REPO}/releases/download/${TAG}"
fi

# $HOME/.config, not XDG_CONFIG_HOME: the quadlet generator scans this fixed path and the
# units resolve %h/.config for runtime.env.
QUADLET_DIR="$HOME/.config/containers/systemd"

# --- scratch space --------------------------------------------------------------------
# Room for the whole release: the image tarballs land here before being loaded. Baked in
# like REPO, being needed before manifest.env arrives; empty means the system default. It
# names the *parent*, so the trap only deletes what we made.
TEMPDIR="${TEMPDIR}"
if [ -n "$TEMPDIR" ]; then
  mkdir -p "$TEMPDIR" || { echo "TEMPDIR '$TEMPDIR' is not usable." >&2; exit 1; }
  WORK="$(mktemp -d -p "$TEMPDIR")"
else
  WORK="$(mktemp -d)"
fi
trap 'rm -rf "$WORK"' EXIT

# Keep in sync with the workflow's matrix and the quadlets/ directory.
IMAGES="postgresql crudman sqlmesh proxy grafana grafana_mcp"
QUADLETS="main.pod postgresql.container crudman.container sftp.container \
  flight.container sqlmesh.container grafana.container grafana_mcp.container proxy.container \
  postgresql_data.volume \
  grafana_data.volume sftp_data.volume \
  proxy_data.volume uploads_data.volume"

# --- progress reporting ---------------------------------------------------------------
# The tools' own progress bars, enabled only when stderr is a terminal: piped into a log
# they would be noise.
if [ -t 2 ]; then
  CURL_PROGRESS="-#"       # transfer bar for the large image tarballs
  PODMAN_QUIET=""          # let podman load draw its layer progress
else
  CURL_PROGRESS="-s"
  PODMAN_QUIET="-q"
fi

# A step heading, so the bars and podman's output below it have a label.
step() { echo; echo "==> $*"; }

# --- preflight: rootless podman needs a usable subuid/subgid range ---------------------
step "Checking prerequisites"
command -v podman >/dev/null || { echo "podman is not installed." >&2; exit 1; }

# Without a subordinate UID/GID range, rootless podman falls back to single-UID mapping
# and any layer needing more than one UID fails later. Asking podman beats grepping
# /etc/subuid, which misses realm-joined users whose ranges come from SSSD/nss.
if ! podman unshare sh -c 'true' >/dev/null 2>&1; then
  echo "Rootless podman cannot set up a user namespace for '$(id -un)'." >&2
  echo "This usually means no subuid/subgid range is mapped. Ask an admin to run:" >&2
  echo "  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)" >&2
  echo "Realm-joined users (name@domain) may need the range added to their directory." >&2
  exit 1
fi

# --- preflight: the journal has to be persistent ---------------------------------------
# journald keeps the services' log history, and without /var/log/journal it holds
# everything in /run/log/journal, which a reboot loses. Ubuntu ships the directory, RHEL
# does not, and creating it needs root, so this only reports and carries on.
#
# tmpfiles applies the group and ACLs journalctl needs; mkdir alone leaves root:root 0755.
# A host pinned to Storage=volatile needs journald.conf edited too.
if [ ! -d /var/log/journal ]; then
  echo "  /var/log/journal does not exist, so the logs are lost on reboot. Ask an admin:" >&2
  echo "    sudo mkdir -p /var/log/journal" >&2
  echo "    sudo systemd-tmpfiles --create --prefix /var/log/journal" >&2
  echo "    sudo journalctl --flush" >&2
  echo "  If they are still gone afterwards, check Storage= in journald.conf." >&2
fi

# --- images: download each tarball with its own curl, then load it --------------------
step "Downloading the release from ${BASE}"
curl -fsSL "${BASE}/manifest.env" -o "${WORK}/manifest.env"
. "${WORK}/manifest.env"   # APP_NAME, SUPERUSER_NAME, CRUDMAN_PATH, GRAFANA_PATH, ...

# --- runtime configuration -------------------------------------------------------------
# The quadlets read this through EnvironmentFile=, and the installer needs SERVER_NAME and
# DEBUG now. An existing file is kept, so a reinstall never overwrites a tuned value, but
# one from an older release can lack a setting this one needs, so those are appended.
APP_CONFIG_DIR="$HOME/.config/${APP_NAME}"
mkdir -p "$APP_CONFIG_DIR"
curl -fsSL "${BASE}/runtime.env" -o "${WORK}/runtime.env"
if [ ! -f "$APP_CONFIG_DIR/runtime.env" ]; then
  cp "${WORK}/runtime.env" "$APP_CONFIG_DIR/runtime.env"
  echo "  wrote ${APP_CONFIG_DIR}/runtime.env"
else
  added=""
  # Every "KEY=" line in the shipped file is a setting this release expects.
  for key in $(sed -n 's/^\([A-Z_][A-Z0-9_]*\)=.*/\1/p' "${WORK}/runtime.env"); do
    if ! grep -q "^${key}=" "$APP_CONFIG_DIR/runtime.env"; then
      grep "^${key}=" "${WORK}/runtime.env" >> "$APP_CONFIG_DIR/runtime.env"
      added="${added} ${key}"
    fi
  done
  if [ -n "$added" ]; then
    echo "  kept ${APP_CONFIG_DIR}/runtime.env, added the new setting(s):${added}"
  else
    echo "  keeping the existing ${APP_CONFIG_DIR}/runtime.env"
  fi
fi
. "$APP_CONFIG_DIR/runtime.env"

# A copy saved with CRLF leaves a carriage return on every value, which would land inside
# the URLs and make DEBUG match neither branch.
SERVER_NAME="$(printf '%s' "${SERVER_NAME:-}" | tr -d '[:space:]')"
DEBUG="$(printf '%s' "${DEBUG:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"

# --- the server name ---------------------------------------------------------------
# The fully qualified host name the system is reached under, or "localhost". Not a
# scheme, a port or a path.
#
# Used verbatim: the certificate is issued for it, Django accepts it and the dropzone pages
# hand it to uploaders. A short name usually fails on a company network, where the browser
# gives it to the web proxy instead of resolving it -- but only a warning, since a short
# name is correct where it does resolve.
case "${SERVER_NAME}" in
  *.*|localhost) : ;;
  *) echo "  ${SERVER_NAME} is not fully qualified; if browsers on your network cannot" >&2
     echo "  reach it by its short name, set SERVER_NAME to the full name (e.g." >&2
     echo "  ${SERVER_NAME}.mycompany.com) in ${APP_CONFIG_DIR}/runtime.env and restart" >&2 ;;
esac

# The address as seen from outside, for the startup check and the cheat sheet. DEBUG picks
# the scheme, as it does for the proxy. The port is named only when the scheme does not
# imply it: printing ":443" would read as something to configure.
if [ "${DEBUG}" = "true" ]; then
  SCHEME="http"; WEB_PORT="${HTTP_PORT}"; SCHEME_PORT=80
else
  SCHEME="https"; WEB_PORT="${HTTPS_PORT}"; SCHEME_PORT=443
fi
if [ "${WEB_PORT}" = "${SCHEME_PORT}" ]; then
  BASE_URL="${SCHEME}://${SERVER_NAME}"
else
  BASE_URL="${SCHEME}://${SERVER_NAME}:${WEB_PORT}"
fi

# --- preflight: the published ports must be bindable and reachable ---------------------
# Read from the runtime.env sourced above, the same file main.pod expands its PublishPort
# lines from, so this checks what will be bound. Neither failure aborts the install -- the
# stack is worth having with only some ports open -- so this reports the fix for this
# host.
PORTS="${HTTP_PORT} ${HTTPS_PORT} ${PG_PORT} ${SFTP_PORT} ${FLIGHT_PORT}"
PORT_HINTS=""   # collected fixes, repeated in the cheat sheet at the end

# A running deployment holds the very ports checked below, so it goes down first.
if systemctl --user is-active main-pod.service >/dev/null 2>&1; then
  echo "Stopping the currently running deployment before install"
  systemctl --user stop main-pod.service >/dev/null 2>&1 || true
fi
step "Checking the published ports"

# 1. Rootless podman may not bind low ports: the kernel reserves everything below
#    net.ipv4.ip_unprivileged_port_start for root. Which those are depends on
#    runtime.env, so they are worked out rather than named.
UNPRIV_START=$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo 1024)
LOW_PORTS=""
for p in $PORTS; do
  [ "$p" -lt "$UNPRIV_START" ] && LOW_PORTS="${LOW_PORTS} ${p}"
done
if [ -n "$LOW_PORTS" ]; then
  # The floor has to clear the lowest of them; anything above stays reserved.
  LOWEST=$(echo $LOW_PORTS | tr ' ' '\n' | sort -n | head -1)
  PORT_HINTS="${PORT_HINTS}
Let rootless podman bind port(s)${LOW_PORTS} (currently reserved below ${UNPRIV_START}):
  echo net.ipv4.ip_unprivileged_port_start=${LOWEST} | sudo tee /etc/sysctl.d/99-${APP_NAME}.conf
  sudo sysctl --system"
  echo "  port(s)${LOW_PORTS} are reserved for root on this host" >&2
fi

# 2. A port already in use makes the pod fail to start with a bind error. The deployment
#    just stopped may still hold one: systemctl returns once the unit is inactive, but
#    podman's port forwarder closes its socket a moment later. So a busy port gets a
#    while to come free.
for p in $PORTS; do
  waited=0
  while command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$p" 2>/dev/null | grep -q ":$p"; do
    [ "$waited" -ge 30 ] && echo "  port ${p} is already in use by another service; free it or edit main.pod" >&2 && break
    sleep 1
    waited=$((waited + 1))
  done
done

# 3. The host firewall: firewalld (RHEL) and ufw (Ubuntu) ship enabled on the supported
#    distributions. The fix commands open a port to everyone, which an operator may want
#    to narrow to a VPN or subnet, so the hint says so.
blocked=""
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  fw=firewalld
  # 80 and 443 are usually opened through firewalld's named http/https services, which
  # --query-port does not see, so ask for the service too.
  for p in $PORTS; do
    case "$p" in 80) svc=http ;; 443) svc=https ;; *) svc="" ;; esac
    firewall-cmd --query-port="${p}/tcp" >/dev/null 2>&1 && continue
    [ -n "$svc" ] && firewall-cmd --query-service="$svc" >/dev/null 2>&1 && continue
    blocked="${blocked} ${p}"
  done
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  fw=ufw
  for p in $PORTS; do
    ufw status 2>/dev/null | grep -qE "^${p}(/tcp)?[[:space:]]+ALLOW" || blocked="${blocked} ${p}"
  done
fi

if [ -n "$blocked" ]; then
  echo "  ${fw} blocks:${blocked}" >&2
  for p in $blocked; do
    case "$fw" in
      firewalld)
        # The named service for the two web ports, as firewalld hosts are usually
        # configured, and the port number for the rest.
        case "$p" in
          80)  cmd="sudo firewall-cmd --permanent --add-service=http && sudo firewall-cmd --reload" ;;
          443) cmd="sudo firewall-cmd --permanent --add-service=https && sudo firewall-cmd --reload" ;;
          *)   cmd="sudo firewall-cmd --permanent --add-port=${p}/tcp && sudo firewall-cmd --reload" ;;
        esac ;;
      ufw) cmd="sudo ufw allow ${p}/tcp" ;;
    esac
    PORT_HINTS="${PORT_HINTS}
Open port ${p} (restrict the source if it should not be reachable from everywhere):
  ${cmd}"
  done
fi

if [ -z "$PORT_HINTS" ]; then
  echo "  ports ${PORTS} are bindable and open"
else
  echo "  the system will start, but some ports stay unreachable until you run:"
  echo "$PORT_HINTS"
fi


# By far the largest download, so they are counted off and curl draws a bar for each.
img_total=$(echo $IMAGES | wc -w)
img_n=0
for img in $IMAGES; do
  img_n=$((img_n + 1))
  echo
  echo "[${img_n}/${img_total}] ${img}"
  curl -fL $CURL_PROGRESS "${BASE}/${img}.tar" -o "${WORK}/${img}.tar"
  podman load $PODMAN_QUIET -i "${WORK}/${img}.tar"
done

# --- quadlets: download each unit file with its own curl, then install it --------------
# A few kilobytes each: no bar, one line saying how many were installed.
step "Installing quadlet unit files"
mkdir -p "$QUADLET_DIR"
for q in $QUADLETS; do
  curl -fsSL "${BASE}/${q}" -o "${WORK}/${q}"
  # Fully rendered at build time, so they install unchanged.
  cp "${WORK}/${q}" "$QUADLET_DIR/$q"
done
echo "  $(echo $QUADLETS | wc -w) unit files installed in ${QUADLET_DIR}"

# --- server-statistics collector: host-side timer and collector ------------------------
# On the host rather than in a container, so it can read the pod's cgroup counters, disk
# IOPS and network egress. Its units live with the other user units.
step "Installing the server-statistics collector"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR" "$APP_CONFIG_DIR/serverstats"

for u in server-stats.service server-stats.timer; do
  curl -fsSL "${BASE}/${u}" -o "${WORK}/${u}"
  cp "${WORK}/${u}" "$SYSTEMD_USER_DIR/$u"
done

curl -fsSL "${BASE}/collect.sh" -o "${WORK}/collect.sh"
install -m 0755 "${WORK}/collect.sh" "$APP_CONFIG_DIR/serverstats/collect.sh"

# --- create the volumes up front so we own their directories --------------------------
# Created here rather than at first container start, so the directories belong to the
# rootless user. The files inside do not always: postgresql and grafana write as a
# container user mapped to a subuid, so reading those from the host needs `podman unshare`.
# Owning them too would need UserNS=keep-id, which the PostgreSQL image does not survive.
step "Creating data volumes"
VOLUMES="postgresql_data grafana_data sftp_data proxy_data uploads_data"
for vol in $VOLUMES; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done
echo "  $(echo $VOLUMES | wc -w) data volumes ready"

# --- machine secrets ------------------------------------------------------------------
# One secret per non-human credential, generated locally with openssl. The superuser
# password is prompted for, so it never lands in the shell history; an empty answer falls
# back to SUPERUSER_DEFAULT_PASSWORD. An existing secret is left as it is.
create_secret() {  # name, value-producing command
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}
read_superuser_password() {
  if [ -t 0 ]; then
    tty_fd=0
  elif [ -r /dev/tty ]; then
    tty_fd=/dev/tty
  else
    echo "No interactive terminal is available; the superuser password could not be read." >&2
    return 1
  fi

  printf 'Set the superuser (admin) password (leave empty for default): ' >&2
  stty -echo <"$tty_fd" 2>/dev/null || true
  if ! IFS= read -r SU_PW <"$tty_fd"; then
    stty echo <"$tty_fd" 2>/dev/null || true
    printf '\n' >&2
    return 1
  fi
  stty echo <"$tty_fd" 2>/dev/null || true

  printf '\n' >&2
  printf '%s' "${SU_PW:-$SUPERUSER_DEFAULT_PASSWORD}"
}
step "Creating secrets"
create_secret "$SECRET_DJANGO_KEY"       "$(openssl rand -hex 32)"
create_secret "$SECRET_CRUDMAN_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_SQLMESH_PASSWORD" "$(openssl rand -hex 32)"
create_secret "$SECRET_GRAFANA_PASSWORD" "$(openssl rand -hex 32)"

# The one credential this script cannot produce, the identity provider issuing it. Created
# anyway, because a quadlet whose Secret= names a missing secret refuses to start; the
# operator replaces the placeholder with the cheat sheet command. A marker rather than "",
# which podman rejects.
create_secret "$SECRET_OIDC_CLIENT" "unconfigured"

if ! podman secret exists "$SECRET_SUPERUSER_PASSWORD" 2>/dev/null; then
  if ! SU_PW="$(read_superuser_password)"; then
    echo "The superuser password was not created because it could not be read." >&2
    exit 1
  fi
  # An empty secret would lock the superuser out, so say what to fix instead.
  if [ -z "$SU_PW" ]; then
    echo "No password was entered and the release has no SUPERUSER_DEFAULT_PASSWORD." >&2
    echo "Run the installer again and enter a password." >&2
    exit 1
  fi
  printf '%s' "$SU_PW" | podman secret create "$SECRET_SUPERUSER_PASSWORD" - >/dev/null
  unset SU_PW
fi

# --- delegate the io cgroup controller so the collector can read disk IOPS/throughput --
# systemd delegates cpu/memory/pids to a user slice but withholds io, so the pod's io.stat
# is absent without this. The drop-in needs root; without it the collector records those
# two metrics as NULL.
IO_DROPIN=/etc/systemd/system/user@.service.d/10-${APP_NAME}-delegate-io.conf
if [ ! -f "$IO_DROPIN" ] && command -v sudo >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null || [ -t 0 ]; then
    sudo mkdir -p "$(dirname "$IO_DROPIN")" 2>/dev/null \
      && printf '[Service]\nDelegate=cpu cpuset io memory pids\n' \
         | sudo tee "$IO_DROPIN" >/dev/null 2>&1 \
      && sudo systemctl daemon-reload 2>/dev/null \
      && echo "Delegated the io cgroup controller (disk IOPS/throughput will be recorded)." \
      || echo "Could not delegate the io controller; disk IOPS/throughput stay unrecorded." >&2
  fi
fi

# --- enable lingering so the pod runs without an active login ------------------------
step "Enabling the system units"
# Without lingering, systemd stops the user manager at the last logout and takes the stack
# with it. Check the result rather than the exit status: where polkit denies the request
# without a way to prompt, loginctl reports success while the setting does not stick.
linger_enabled() { [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" = "yes" ]; }
if ! linger_enabled; then
  # On most hosts polkit lets a user linger themselves; where it is denied, escalate as
  # the io drop-in above does.
  loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
  if ! linger_enabled && command -v sudo >/dev/null 2>&1; then
    if sudo -n true 2>/dev/null || [ -t 0 ]; then
      sudo loginctl enable-linger "$(id -un)" >/dev/null 2>&1 || true
    fi
  fi
  if linger_enabled; then
    echo "Enabled lingering for $(id -un) (${APP_NAME} keeps running after you log out)."
  else
    echo "Could not enable lingering for $(id -un); ${APP_NAME} will stop when you log out." >&2
  fi
fi

# enable-linger starts user@$(id -u).service asynchronously, so on a user with no manager
# yet the systemctl calls below can beat its D-Bus socket into existence and fail with
# "Connection reset by peer". Any answer counts as ready, "degraded" included: that reports
# the health of the units, not of the connection.
i=0
while [ "$i" -lt 30 ] && ! systemctl --user show -p Version >/dev/null 2>&1; do
  i=$((i + 1))
  sleep 1
done

systemctl --user daemon-reload

# --now both enables the timer for future boots and starts sampling immediately.
systemctl --user enable --now server-stats.timer 2>/dev/null || true

# --- start the stack ------------------------------------------------------------------
# The pod unit pulls in the rest of the stack, but starting each service explicitly gives
# the same per-service health output the integration tests print.
#
# The container units are stopped first, or a reinstall would leave the old containers
# running on the previous images. A failure here is not fatal: everything is installed by
# now, and the cheat sheet says how to start the system by hand.
step "Starting ${APP_NAME}"
for u in $QUADLETS; do
  case "$u" in
    *.container) systemctl --user stop "$(basename "$u" .container).service" 2>/dev/null || true ;;
  esac
done

UNITS="postgresql crudman sftp flight sqlmesh grafana grafana_mcp proxy"
stack_start="$(date +%s)"
if systemctl --user restart main-pod.service 2>/dev/null; then
  for u in $UNITS; do
    printf '  %-12s ' "$u"
    i=0
    while [ "$i" -lt 60 ] && ! systemctl --user is-active "${u}.service" >/dev/null 2>&1; do
      i=$((i + 1))
      sleep 1
    done
    if systemctl --user is-active "${u}.service" >/dev/null 2>&1; then
      printf 'healthy (%ss)\n' "$(( $(date +%s) - stack_start ))"
    elif ! systemctl --user cat "${u}.service" >/dev/null 2>&1; then
      # No unit at all: the quadlet generator rejected the file, so there is nothing to
      # start and nothing in the journal -- which looks like a crashed service.
      echo "no unit generated -- run: /usr/libexec/podman/quadlet -user -dryrun" >&2
    elif [ "$u" = "proxy" ] && [ "${DEBUG}" != "true" ]; then
      # The likeliest reason in production, and one the operator can fix without a log.
      echo "not active -- the TLS certificate is most likely missing." >&2
      echo "               The proxy names the file it did not find and where it looked:" >&2
      echo "                 journalctl --user -u proxy -n 10" >&2
    else
      echo "not active -- see: journalctl --user -u ${u}" >&2
    fi
  done

  # --- which certificate is in use ------------------------------------------------------
  # Naming the files found rules out the usual mix-ups: a certificate in the wrong
  # directory, or the right one holding last year's pair. The directory comes from the
  # installed unit, where systemd has expanded CERTIFICATE_HINT already.
  if [ "${DEBUG}" != "true" ]; then
    certdir="$(systemctl --user show -p Environment --value proxy.service 2>/dev/null \
                 | tr ' ' '\n' | sed -n 's/^CERTIFICATE_HINT=//p')"
    for f in ${certdir:+fullchain.pem privkey.pem}; do
      if [ -f "${certdir}/${f}" ]; then
        printf '  %-12s %s\n' "certificate" "${certdir}/${f}"
      else
        printf '  %-12s %s MISSING from %s\n' "certificate" "$f" "${certdir}/" >&2
      fi
    done
  fi

  # --- the pages must actually be served ------------------------------------------------
  # An active unit is not a served page: a proxy whose upstream is unreachable still
  # satisfies systemd. --insecure because the certificate may come from a company CA this
  # host does not trust; this checks reachability, not trust. No -f, which would hide the
  # status code. --retry-connrefused bridges the moment between the unit going active and
  # nginx accepting connections, on a small budget: curl also retries a 502, which is the
  # dead-upstream answer this check exists to report.
  #
  # Addressed to SERVER_NAME and pinned to loopback with --resolve rather than fetching
  # localhost: the certificate is issued for SERVER_NAME, and against any other name the
  # handshake can fail outright, which --insecure does not cover. The request never leaves
  # the host, so this works before DNS for SERVER_NAME resolves here.
  #
  # Diagnostic only, and must never end the install. Under `set -e` a bare
  # `code="$(curl ...)"` would abort on curl's exit status before the cheat sheet is
  # printed, so curl runs as an `if` condition, which `set -e` exempts.
  served=true
  # An empty value would make --resolve malformed and turn this into a false alarm.
  probe_host="${SERVER_NAME:-localhost}"
  for app in "${CRUDMAN_PATH}" "${GRAFANA_PATH}"; do
    if code="$(curl -s --insecure -o /dev/null -w '%{http_code}' --max-time 10 \
                 --retry 5 --retry-delay 1 --retry-connrefused \
                 --resolve "${probe_host}:443:127.0.0.1" \
                 --resolve "${probe_host}:80:127.0.0.1" \
                 "${SCHEME}://${probe_host}/${app}/" 2>/dev/null)"; then
      why=""
    else
      # No answer at all -- a handshake failure or a reset, where the status code is
      # empty and only the exit status says what happened.
      why=", curl exit $?"
    fi
    case "$code" in
      200|30[1237]) printf '  %-12s reachable at %s\n' "$app" "${BASE_URL}/${app}/" ;;
      *) served=false
         printf '  %-12s NOT reachable (%s)\n' "$app" "${code:-no response}${why}" >&2 ;;
    esac
  done
  if [ "$served" = "true" ]; then
    echo "  system is up and running"
  else
    echo "  the services are running but the proxy does not serve them; check:" >&2
    echo "    journalctl --user -u proxy" >&2
  fi
else
  echo "  could not start main-pod.service; start it manually (see the cheat sheet)." >&2
  # A refused port bind is the usual cause when the preflight found something.
  [ -n "$PORT_HINTS" ] && echo "  most likely a port could not be bound -- see the port hints above." >&2
fi

# --- helpfile + cheat sheet -----------------------------------------------------------
# Kept in the user's home as well as printed. Lower-cased with tr rather than
# "${APP_NAME,,}", a bash extension dash would abort on; uninstall.sh derives it the same
# way.
HELP="$HOME/$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]')-help.txt"
EDITOR_CMD="${EDITOR:-${VISUAL:-nano}}"

# The login is deliberately absent: the superuser password must not be written to a file
# that stays in $HOME. The port fixes need root, so they go into the cheat sheet as well as
# the install output, where an operator would have to scroll back for them.
if [ -n "$PORT_HINTS" ]; then
  PORT_SECTION="Ports that are still blocked -- run these (as an admin) to reach the system:
${PORT_HINTS}

"
else
  PORT_SECTION=""
fi

cat > "$HELP" <<EOF
${APP_NAME} Cheat sheet
============================

  Admin panel:  ${BASE_URL}/${CRUDMAN_PATH}/
  Model docs:   ${BASE_URL}/${CRUDMAN_PATH}/docs/
  Grafana:      ${BASE_URL}/${GRAFANA_PATH}/
  PostgreSQL:   host=${SERVER_NAME} port=${PG_PORT} dbname=${PG_DATABASE} user=${SUPERUSER_NAME}
                psql "host=${SERVER_NAME} port=${PG_PORT} dbname=${PG_DATABASE} user=${SUPERUSER_NAME}"

${PORT_SECTION}Follow the combined live log of all components:
  journalctl --user -f -u main-pod -u postgresql -u crudman -u sftp -u flight -u sqlmesh -u grafana -u proxy

Shut the system down:
  systemctl --user stop main-pod.service

Start the system up again:
  systemctl --user start main-pod.service

Run a database backup now:
  podman exec postgresql sh -c 'pg_dumpall -U "\$POSTGRES_USER"' > backup-\$(date +%F).sql

Volume paths (cd into them to inspect data):
  postgresql: $(podman volume inspect postgresql_data -f '{{.Mountpoint}}')
  grafana:    $(podman volume inspect grafana_data -f '{{.Mountpoint}}')
  proxy:      $(podman volume inspect proxy_data -f '{{.Mountpoint}}')
  sftp:       $(podman volume inspect sftp_data -f '{{.Mountpoint}}')
  uploads:    $(podman volume inspect uploads_data -f '{{.Mountpoint}}')

The postgresql and grafana volumes are written by a user inside the container, so
reading their contents from the host needs: podman unshare ls <path>

Edit the runtime configuration (SERVER_NAME, DEBUG, the published ports, single sign-on).
The services read it when they start, so restart them to pick a change up:
  ${EDITOR_CMD} \$HOME/.config/${APP_NAME}/runtime.env
  systemctl --user restart main-pod.service

Set the single sign-on client secret (the identity provider issues it). Repeat this when
it expires -- an expired secret fails every sign-in at once:
  printf '%s' '<secret>' | podman secret create --replace ${SECRET_OIDC_CLIENT} -
  systemctl --user restart main-pod.service

Uninstall the system (asks before deleting the data volumes and secrets):
  curl -fsSL ${REPO}/releases/latest/download/uninstall.sh | bash
EOF

cat "$HELP"
echo
echo "This cheat sheet is saved at ${HELP}."
