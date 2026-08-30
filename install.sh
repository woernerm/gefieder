#!/bin/sh
# Installer for a GitHub release.
#
# Run it straight from a release without a checkout:
#
#   curl -fsSL ${REPO}/releases/latest/download/install.sh | bash
#
# It downloads each release asset with its own curl command, loads the image tarballs
# into rootless podman, installs the rendered quadlets, creates the machine secrets, and
# prints a cheat sheet. The values baked into the images at build time (APP_NAME, paths,
# the superuser name) are recorded in the release's manifest.env, which is sourced below.
set -e

# --- where the release lives ----------------------------------------------------------
# Baked in from buildtime.env when the release is built, so this works against an
# enterprise GitHub instance as well as github.com. No default: guessing would silently
# pull someone else's release. From a checkout, set it (and TAG, to pin a version):
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

# Deliberately $HOME/.config and not XDG_CONFIG_HOME: the quadlet generator scans this
# fixed path, and the units resolve %h/.config for runtime.env. An XDG_CONFIG_HOME set in
# the installing shell would put the files where nothing looks.
QUADLET_DIR="$HOME/.config/containers/systemd"

# --- scratch space --------------------------------------------------------------------
# Room for the whole release, since the image tarballs land here before being loaded.
# Baked in like REPO, being needed before manifest.env is downloaded into it; empty means
# the system default. It names the *parent*, so the trap only ever deletes what we made.
TEMPDIR="${TEMPDIR}"
if [ -n "$TEMPDIR" ]; then
  mkdir -p "$TEMPDIR" || { echo "TEMPDIR '$TEMPDIR' is not usable." >&2; exit 1; }
  WORK="$(mktemp -d -p "$TEMPDIR")"
else
  WORK="$(mktemp -d)"
fi
trap 'rm -rf "$WORK"' EXIT

# The images built and saved by the workflow, and the unit files it ships. Keep these in
# sync with the workflow's matrix and the quadlets/ directory.
IMAGES="postgresql crudman sqlmesh proxy grafana"
QUADLETS="main.pod postgresql.container crudman.container sftp.container \
  flight.container sqlmesh.container grafana.container proxy.container \
  postgresql_data.volume \
  grafana_data.volume sftp_data.volume \
  proxy_data.volume uploads_data.volume"

# --- progress reporting ---------------------------------------------------------------
# The tools' own facilities rather than a hand-rolled bar. Both write to the terminal, so
# they are enabled only when stderr is one; piped into a log or run from CI the bars would
# be noise.
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
# and any layer needing more than one UID fails later with a confusing error. Asking
# podman itself beats grepping /etc/subuid, which misses realm-joined users (name@domain)
# whose ranges come from SSSD/nss: `unshare` succeeds only if the namespace can be set up.
if ! podman unshare sh -c 'true' >/dev/null 2>&1; then
  echo "Rootless podman cannot set up a user namespace for '$(id -un)'." >&2
  echo "This usually means no subuid/subgid range is mapped. Ask an admin to run:" >&2
  echo "  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)" >&2
  echo "Realm-joined users (name@domain) may need the range added to their directory." >&2
  exit 1
fi

# --- preflight: the journal has to be persistent ---------------------------------------
# journald is the only place the services' log history lives, and without /var/log/journal
# it keeps everything in /run/log/journal, so a reboot loses it. Ubuntu ships the
# directory, RHEL does not. Creating it needs root, so this only reports and carries on.
#
# tmpfiles applies the group and ACLs journalctl needs; mkdir alone leaves root:root 0755.
# That suffices under the default Storage=auto; a host pinned to Storage=volatile needs
# journald.conf edited too.
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
# The quadlets read this file through EnvironmentFile=, and the installer needs SERVER_NAME
# and DEBUG now, for the addresses it prints and the checks it runs. An existing file is
# kept so a reinstall never overwrites a tuned value, but one written by an older release
# can lack a setting this one needs: append those rather than take "exists" for "current".
# An absent SERVER_NAME would yield an empty address.
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

# The file is edited by hand, and a copy saved with CRLF leaves a carriage return on every
# value: it would land inside the URLs and make DEBUG match neither branch. Strip it here
# once, as the Django settings and the proxy entrypoint do.
SERVER_NAME="$(printf '%s' "${SERVER_NAME:-}" | tr -d '[:space:]')"
DEBUG="$(printf '%s' "${DEBUG:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"

# --- the server name ---------------------------------------------------------------
# The fully qualified host name the system is reached under, e.g. "abc123.mycompany.com",
# or "localhost" for a local system. Not a scheme, a port or a path.
#
# Used verbatim: the certificate is issued for it, Django accepts it and the dropzone
# pages hand it to uploaders. A short name usually fails on a company network, where the
# browser gives it to the web proxy instead of resolving it. Only a warning, because a
# short name is correct where it does resolve.
case "${SERVER_NAME}" in
  *.*|localhost) : ;;
  *) echo "  ${SERVER_NAME} is not fully qualified; if browsers on your network cannot" >&2
     echo "  reach it by its short name, set SERVER_NAME to the full name (e.g." >&2
     echo "  ${SERVER_NAME}.mycompany.com) in ${APP_CONFIG_DIR}/runtime.env and restart" >&2 ;;
esac

# The address as seen from outside, not "localhost", for the startup check and the cheat
# sheet. DEBUG picks the scheme, as it does for the proxy. The port is named only when the
# scheme does not imply it: printing ":443" would read as something to configure.
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
# lines from, so this checks what will actually be bound. Both failure modes below are
# silent until someone's browser times out. Neither aborts the install — the stack is
# worth having with only some ports open — so this reports the fix for *this* host.
PORTS="${HTTP_PORT} ${HTTPS_PORT} ${PG_PORT} ${SFTP_PORT} ${FLIGHT_PORT}"
PORT_HINTS=""   # collected fixes, repeated in the cheat sheet at the end

# A running deployment holds the very ports checked below, so it goes down first.
if systemctl --user is-active main-pod.service >/dev/null 2>&1; then
  echo "Stopping the currently running deployment before install"
  systemctl --user stop main-pod.service >/dev/null 2>&1 || true
fi
step "Checking the published ports"

# 1. Rootless podman may not bind low ports: the kernel reserves everything below
#    net.ipv4.ip_unprivileged_port_start (1024 by default) for root. Which ports those
#    are depends on runtime.env, so they are worked out rather than named.
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

# 2. A port already in use by another service makes the pod fail to start with a bind
#    error, worth catching before that happens. The deployment just stopped above may still
#    hold one: systemctl returns once the unit is inactive, but podman's rootless port
#    forwarder closes its listening socket a moment later. So give a busy port a while to
#    come free -- what is still held afterwards really does belong to something else.
for p in $PORTS; do
  waited=0
  while command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$p" 2>/dev/null | grep -q ":$p"; do
    [ "$waited" -ge 30 ] && echo "  port ${p} is already in use by another service; free it or edit main.pod" >&2 && break
    sleep 1
    waited=$((waited + 1))
  done
done

# 3. The host firewall: firewalld (RHEL) and ufw (Ubuntu) are the two that ship enabled
#    on the supported distributions. The fix commands open a port to everyone, and an
#    operator may want to restrict the database, SFTP and Flight ports to a VPN or subnet
#    instead, so the hint says so rather than pretending one command suits every case.
blocked=""
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  fw=firewalld
  # 80 and 443 are usually opened through firewalld's named http/https services rather
  # than by port number, and --query-port does not see those, so ask for the service too
  # before calling the port blocked.
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
        # Use the named service for the two web ports, which is how firewalld hosts are
        # normally configured, and the port number for the rest.
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


# The image tarballs are by far the largest download; count them off so a stalled transfer
# is obvious, and let curl draw its bar for each one.
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
# These are a few kilobytes each: no bar, just one line saying how many were installed.
step "Installing quadlet unit files"
mkdir -p "$QUADLET_DIR"
for q in $QUADLETS; do
  curl -fsSL "${BASE}/${q}" -o "${WORK}/${q}"
  # The units ship fully rendered (the image names and paths were substituted at build
  # time, the runtime values come from runtime.env), so they install unchanged.
  cp "${WORK}/${q}" "$QUADLET_DIR/$q"
done
echo "  $(echo $QUADLETS | wc -w) unit files installed in ${QUADLET_DIR}"

# --- server-statistics collector: host-side timer and collector ------------------------
# The collector runs on the host (not in a container) so it can read the pod's cgroup
# counters, disk IOPS and network egress. Its systemd user units live with the other user
# units; the script itself goes under ~/.config/<APP_NAME>/serverstats/.
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
# Creating them here rather than at first container start means the directories belong to
# the rootless user. The files inside do not always: postgresql and grafana write as a
# container user mapped to a subuid, so reading those from the host needs `podman
# unshare`. Owning them too would need UserNS=keep-id, which the PostgreSQL image does not
# survive. One volume per service that keeps state, matching the *.volume quadlets.
step "Creating data volumes"
VOLUMES="postgresql_data grafana_data sftp_data proxy_data uploads_data"
for vol in $VOLUMES; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done
echo "  $(echo $VOLUMES | wc -w) data volumes ready"

# --- machine secrets ------------------------------------------------------------------
# One secret per non-human credential, generated locally with openssl. The superuser
# password is prompted for instead, so it never lands in the shell history; an empty
# answer falls back to the well-known SUPERUSER_DEFAULT_PASSWORD meant for trying the
# system out. A secret that already exists is left as is.
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

# The one credential this script cannot produce: the identity provider issues it, and at
# first install there usually is no provider yet. Created anyway, because a quadlet whose
# Secret= names a missing secret refuses to start the container; the operator replaces the
# placeholder with the cheat sheet command. A marker rather than "", which podman rejects.
create_secret "$SECRET_OIDC_CLIENT" "unconfigured"

if ! podman secret exists "$SECRET_SUPERUSER_PASSWORD" 2>/dev/null; then
  if ! SU_PW="$(read_superuser_password)"; then
    echo "The superuser password was not created because it could not be read." >&2
    exit 1
  fi
  # Empty only if the release manifest carries no default either; an empty secret would
  # lock the superuser out, so say what to fix rather than create one.
  if [ -z "$SU_PW" ]; then
    echo "No password was entered and the release has no SUPERUSER_DEFAULT_PASSWORD." >&2
    echo "Run the installer again and enter a password." >&2
    exit 1
  fi
  printf '%s' "$SU_PW" | podman secret create "$SECRET_SUPERUSER_PASSWORD" - >/dev/null
  unset SU_PW
fi

# --- delegate the io cgroup controller so the collector can read disk IOPS/throughput --
# systemd delegates cpu/memory/pids to a user slice by default but withholds io, so the
# pod's io.stat is absent without this. The drop-in needs root: attempt it with sudo and
# carry on if unavailable, the collector then recording those two metrics as NULL.
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
# Without lingering, systemd stops the user manager as soon as the user's last session ends,
# taking the whole stack down with it at logout. Check the result rather than the exit
# status: where polkit denies the request without a way to prompt, loginctl still reports
# success while the setting does not stick, and the deployment would die silently.
linger_enabled() { [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" = "yes" ]; }
if ! linger_enabled; then
  # Unprivileged first: on most hosts polkit lets a user linger themselves, so this is all
  # it takes. Where it is denied, escalate the same way the io drop-in above does rather
  # than leaving the deployment to die at the next logout.
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

# enable-linger only asks logind to start user@$(id -u).service; that start is asynchronous,
# so on a user with no running manager yet (a service account deployed over SSH, a first
# boot) the systemctl calls below can beat the manager's D-Bus socket into existence and
# fail with "Connection reset by peer". Wait for the bus to answer before using it.
# Any answer counts as ready, including "degraded" -- that reports the health of the units,
# not of the connection, and a host with an unrelated failed unit is still perfectly able to
# run the stack. Only a manager that cannot be reached at all keeps us waiting.
i=0
while [ "$i" -lt 30 ] && ! systemctl --user show -p Version >/dev/null 2>&1; do
  i=$((i + 1))
  sleep 1
done

systemctl --user daemon-reload

# Enable the server-statistics timer so sampling starts (and resumes after a reboot)
# without a manual step. enable --now both enables it for future boots and starts it now.
systemctl --user enable --now server-stats.timer 2>/dev/null || true

# --- start the stack ------------------------------------------------------------------
# The pod unit pulls in the rest of the stack, but starting each service unit explicitly
# gives the installer the same per-service health output the integration tests print.
#
# The container units are stopped first because a reinstall over a running stack would
# otherwise leave the old containers running on the previous images. Stopping them lets
# the services restart cleanly from the images just loaded. On a first install there is
# nothing to stop and this does nothing. A failure here is not fatal -- everything is
# installed by now, and the cheat sheet below says how to start the system by hand.
step "Starting ${APP_NAME}"
for u in $QUADLETS; do
  case "$u" in
    *.container) systemctl --user stop "$(basename "$u" .container).service" 2>/dev/null || true ;;
  esac
done

UNITS="postgresql crudman sftp flight sqlmesh grafana proxy"
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
      # No unit at all: the quadlet generator rejected the file (a key this podman does
      # not know skips the whole unit) so there is nothing to start and nothing in the
      # journal either -- which looks the same as a crashed service unless we say so.
      echo "no unit generated -- run: /usr/libexec/podman/quadlet -user -dryrun" >&2
    elif [ "$u" = "proxy" ] && [ "${DEBUG}" != "true" ]; then
      # By far the most likely reason in production, and the one the operator can fix
      # without reading a log: the entrypoint exits when the certificate is not there.
      echo "not active -- the TLS certificate is most likely missing." >&2
      echo "               The proxy names the file it did not find and where it looked:" >&2
      echo "                 journalctl --user -u proxy -n 10" >&2
    else
      echo "not active -- see: journalctl --user -u ${u}" >&2
    fi
  done

  # --- which certificate is in use ------------------------------------------------------
  # Naming the files actually found rules out the usual mix-ups: a certificate left in the
  # wrong directory, or the right one holding last year's pair. The directory comes from
  # the installed unit, where systemd has already expanded the "%h" the quadlet wrote into
  # CERTIFICATE_HINT, so nothing here has to parse a specifier. Production only, since
  # DEBUG=true serves plain HTTP and mounts no certificate.
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
  # satisfies systemd, so fetch both applications the way a browser does. --insecure
  # because the certificate may come from a company CA this host does not trust; this
  # checks reachability, not trust. No -f, which would hide the status code that makes a
  # failure diagnosable. --retry-connrefused bridges the second or two between the unit
  # going active and nginx accepting connections. The budget is deliberately small: curl
  # also retries a 502, and that is the dead-upstream answer this check exists to report
  # rather than wait out.
  #
  # The request is addressed to SERVER_NAME and pinned to the loopback address with
  # --resolve, rather than fetching localhost: the certificate is issued for SERVER_NAME,
  # and against any other name the handshake can fail outright -- no status code, curl
  # exits non-zero -- which is what --insecure alone does not cover. Addressed this way
  # the name matches the certificate and nginx's server_name, while the request never
  # leaves the host, so the check also works before DNS for SERVER_NAME resolves here.
  #
  # This whole check is diagnostic: it must never end the install, which is finished by
  # now and still owes the operator its cheat sheet. Under `set -e` a bare
  # `code="$(curl ...)"` would abort silently on curl's exit status, before the cheat sheet
  # is printed -- so curl runs as an `if` condition, which `set -e` exempts, and a non-zero
  # exit is reported instead of being fatal.
  served=true
  # Never empty in practice (runtime.env ships SERVER_NAME=localhost), but an empty value
  # would make --resolve malformed and turn the check into a false alarm.
  probe_host="${SERVER_NAME:-localhost}"
  for app in "${CRUDMAN_PATH}" "${GRAFANA_PATH}"; do
    if code="$(curl -s --insecure -o /dev/null -w '%{http_code}' --max-time 10 \
                 --retry 5 --retry-delay 1 --retry-connrefused \
                 --resolve "${probe_host}:443:127.0.0.1" \
                 --resolve "${probe_host}:80:127.0.0.1" \
                 "${SCHEME}://${probe_host}/${app}/" 2>/dev/null)"; then
      why=""
    else
      # curl never reached an answer -- a handshake failure or a reset, where the status
      # code above is empty and the exit status is the only thing that says what happened.
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
  # A refused port bind is the usual cause when the preflight above found something, so
  # point at that rather than leaving the operator with journalctl and a guess.
  [ -n "$PORT_HINTS" ] && echo "  most likely a port could not be bound -- see the port hints above." >&2
fi

# --- helpfile + cheat sheet -----------------------------------------------------------
# Store the cheat sheet in the user's home so it is available later, and print it now. The
# name is lower-cased with tr rather than with "${APP_NAME,,}": that expansion is a bash
# extension, and this script declares /bin/sh, which is dash on Ubuntu and would abort with
# "Bad substitution". uninstall.sh derives the same name the same way.
HELP="$HOME/$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]')-help.txt"
EDITOR_CMD="${EDITOR:-${VISUAL:-nano}}"

# The login is deliberately absent below: the superuser password was entered above (or
# already existed as a secret) and must not be written to a file that stays in $HOME.
# The port fixes found by the preflight need root, so they cannot be applied here. They go
# into the cheat sheet as well as the install output: an operator who has to fetch an admin
# to run them should not have to scroll back through the install log to find them.
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
