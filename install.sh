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
# REPO is the full URL of the repository the release lives in; it is baked in from
# buildtime.env when the release is built, so this installer works against an enterprise
# GitHub instance as well as github.com. There is no default: guessing a repository would
# silently pull someone else's release. Set REPO (and optionally TAG for a pinned
# version) when running the installer from a checkout, e.g.
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

QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"

# --- scratch space --------------------------------------------------------------------
# The image tarballs are downloaded here before being loaded, so this needs room for the
# whole release. TEMPDIR is baked in from buildtime.env (like REPO above) because it is
# needed before manifest.env is downloaded into it; empty means the system default. It
# names the *parent*: the scratch directory below is still one we created ourselves, so
# the trap only ever deletes our own files, never the operator's directory.
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
  grafana_data.volume crudman_data.volume sftp_data.volume sqlmesh_data.volume \
  proxy_data.volume uploads_data.volume"

# --- progress reporting ---------------------------------------------------------------
# Long steps report progress with the tools' own facilities rather than a hand-rolled bar:
# curl's -# draws the transfer bar, podman load draws its own layer progress. Both write to
# the terminal, so they are only enabled when stderr is one -- piped into a log or run from
# CI the bars would just be noise, and curl falls back to -s there.
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

# Without a range of subordinate UIDs/GIDs, rootless podman falls back to single-UID
# mapping: a trivial image may load, but any layer needing more than one UID fails later
# with a confusing error. Rather than grep /etc/subuid (which misses realm-joined users
# like name@domain, whose ranges come from SSSD/nss and are not listed there), ask podman
# itself: `unshare` only succeeds when a real user namespace with the range can be set up.
if ! podman unshare sh -c 'true' >/dev/null 2>&1; then
  echo "Rootless podman cannot set up a user namespace for '$(id -un)'." >&2
  echo "This usually means no subuid/subgid range is mapped. Ask an admin to run:" >&2
  echo "  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)" >&2
  echo "Realm-joined users (name@domain) may need the range added to their directory." >&2
  exit 1
fi

# --- images: download each tarball with its own curl, then load it --------------------
step "Downloading the release from ${BASE}"
curl -fsSL "${BASE}/manifest.env" -o "${WORK}/manifest.env"
. "${WORK}/manifest.env"   # APP_NAME, SUPERUSER_NAME, CRUDMAN_PATH, GRAFANA_PATH, ...

# --- preflight: the published ports must be bindable and reachable ---------------------
# The pod publishes 80, 443, 5432, 2222 and 8815 (see quadlets/main.pod). Two things can
# stop them working, and both are silent until someone's browser times out, so they are
# checked here rather than left to be discovered later. Neither aborts the install: the
# stack is still worth having with, say, only the database port open, so this reports what
# is wrong and prints the exact command that fixes it on *this* host.
if systemctl --user is-active main-pod.service >/dev/null 2>&1; then
  echo "Stopping the currently running deployment before install"
  systemctl --user stop main-pod.service >/dev/null 2>&1 || true
fi
step "Checking the published ports"
PORTS="80 443 5432 2222 8815"
PORT_HINTS=""   # collected fixes, repeated in the cheat sheet at the end

# 1. Rootless podman may not bind low ports. The kernel reserves everything below
#    net.ipv4.ip_unprivileged_port_start (1024 by default) for root, so 80 and 443 fail
#    with "permission denied" while 5432/2222/8815 are fine.
UNPRIV_START=$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo 1024)
if [ "$UNPRIV_START" -gt 80 ]; then
  PORT_HINTS="${PORT_HINTS}
Let rootless podman bind ports 80 and 443 (currently reserved below ${UNPRIV_START}):
  echo net.ipv4.ip_unprivileged_port_start=80 | sudo tee /etc/sysctl.d/99-${APP_NAME}.conf
  sudo sysctl --system"
  echo "  ports 80 and 443 are reserved for root on this host" >&2
fi

# 2. A port already in use by another service would make the pod fail to start with a
#    bind error, which is worth catching before that happens.
for p in $PORTS; do
  if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$p" 2>/dev/null | grep -q ":$p"; then
    echo "  port ${p} is already in use by another service; free it or edit main.pod" >&2
  fi
done

# 3. The host firewall. firewalld (RHEL) and ufw (Ubuntu) are the two that ship enabled on
#    the supported distributions; if neither is installed, or it is installed but inactive,
#    nothing is blocking and there is nothing to report. The fix commands below open a port
#    to everyone -- for the database, SFTP and Flight ports an operator may well want to
#    restrict the source to a VPN or a subnet instead, so the hint says so rather than
#    pretending one command suits every deployment.
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
  cp "${WORK}/${q}" "$QUADLET_DIR/$q"
done
echo "  $(echo $QUADLETS | wc -w) unit files installed in ${QUADLET_DIR}"

# --- server-statistics collector: host-side timer, collector and default config --------
# The collector runs on the host (not in a container) so it can read the pod's cgroup
# counters, disk IOPS and network egress. Its systemd user units live with the other user
# units; the script and the runtime config live under ~/.config/<APP_NAME>/.
step "Installing the server-statistics collector"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/${APP_NAME}"
mkdir -p "$SYSTEMD_USER_DIR" "$APP_CONFIG_DIR/serverstats"

for u in server-stats.service server-stats.timer; do
  curl -fsSL "${BASE}/${u}" -o "${WORK}/${u}"
  cp "${WORK}/${u}" "$SYSTEMD_USER_DIR/$u"
done

curl -fsSL "${BASE}/collect.sh" -o "${WORK}/collect.sh"
install -m 0755 "${WORK}/collect.sh" "$APP_CONFIG_DIR/serverstats/collect.sh"

# Ship the default runtime config only if the user has none yet, so a reinstall never
# overwrites an interval the operator has already tuned.
if [ ! -f "$APP_CONFIG_DIR/runtime.env" ]; then
  curl -fsSL "${BASE}/runtime.env" -o "${WORK}/runtime.env"
  cp "${WORK}/runtime.env" "$APP_CONFIG_DIR/runtime.env"
fi

# --- create the volumes up front so we own their contents -----------------------------
# Creating the volumes here (rather than letting the first container start create them)
# means the directories are owned by the rootless user from the start, so writing logs
# and data needs no `podman unshare`. The container's own user inside its namespace maps
# back to this user.
# One data volume per service, matching the VolumeName= in the *.volume quadlets. The
# crudman/sqlmesh/proxy volumes currently hold only the log the entrypoint tees, but are
# general per-service data volumes.
step "Creating data volumes"
VOLUMES="postgresql_data grafana_data crudman_data sftp_data sqlmesh_data proxy_data uploads_data"
for vol in $VOLUMES; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done
echo "  $(echo $VOLUMES | wc -w) data volumes ready"

# --- machine secrets ------------------------------------------------------------------
# One secret per non-human credential, generated locally with openssl. Human logins (the
# superuser) are NOT created here: the superuser password is prompted once below so it
# never lands in a file or the shell history. A secret that already exists is left as is.
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

  printf 'Set the superuser (admin) password: ' >&2
  stty -echo <"$tty_fd" 2>/dev/null || true
  if ! IFS= read -r SU_PW <"$tty_fd"; then
    stty echo <"$tty_fd" 2>/dev/null || true
    printf '\n' >&2
    return 1
  fi
  stty echo <"$tty_fd" 2>/dev/null || true

  printf '\n' >&2
  printf '%s' "$SU_PW"
}
step "Creating secrets"
create_secret django_secret_key "$(openssl rand -hex 32)"
create_secret crudman_password  "$(openssl rand -hex 32)"
create_secret sqlmesh_password  "$(openssl rand -hex 32)"
create_secret grafana_password  "$(openssl rand -hex 32)"

if ! podman secret exists superuser_password 2>/dev/null; then
  if ! SU_PW="$(read_superuser_password)"; then
    echo "The superuser password was not created because it could not be read." >&2
    exit 1
  fi
  printf '%s' "$SU_PW" | podman secret create superuser_password - >/dev/null
  unset SU_PW
fi

# --- delegate the io cgroup controller so the collector can read disk IOPS/throughput --
# systemd delegates cpu/memory/pids to a user slice by default but withholds io, so the
# pod's io.stat (the source of the disk IOPS and read/write-speed figures) is absent
# without this. The drop-in needs root; attempt it with sudo and carry on if unavailable
# (the collector then records those two metrics as NULL, the others still work). A kernel
# that does not expose per-cgroup io at all (some WSL2 builds) is unaffected by this.
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
# Without lingering, systemd stops the user manager as soon as the last session of the user
# ends, taking the whole stack down with it at logout. Check the result rather than the exit
# status: where polkit denies the request without a way to prompt, loginctl still reports
# success but the setting does not stick, and the deployment would silently die at logout.
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
# Quadlet gives the pod unit a Wants=/Before= on every container unit, so starting
# main-pod.service brings the whole system up in one command.
#
# The container units are stopped first because that Wants= is a weak dependency: on a
# reinstall over a running stack, restarting the pod alone would leave the old containers
# running on the previous images. Stopping them lets the pod start them again from the
# images just loaded. On a first install there is nothing to stop and this does nothing.
# A failure here is not fatal -- everything is installed by now, and the cheat sheet below
# says how to start the system by hand.
step "Starting ${APP_NAME}"
for u in $QUADLETS; do
  case "$u" in
    *.container) systemctl --user stop "$(basename "$u" .container).service" 2>/dev/null || true ;;
  esac
done

if systemctl --user restart main-pod.service 2>/dev/null; then
  echo "  started; the database needs a few seconds to initialise on the first run"
else
  echo "  could not start main-pod.service; start it manually (see the cheat sheet)." >&2
  # A refused port bind is the usual cause when the preflight above found something, so
  # point at that rather than leaving the operator with journalctl and a guess.
  [ -n "$PORT_HINTS" ] && echo "  most likely a port could not be bound -- see the port hints above." >&2
fi

# --- helpfile + cheat sheet -----------------------------------------------------------
# Store the cheat sheet in the user's home so it is available later, and print it now.
HELP="$HOME/${APP_NAME}-help.txt"
EDITOR_CMD="${EDITOR:-${VISUAL:-nano}}"

# The addresses as seen from outside, not "localhost": SERVER_NAME is the host name the
# proxy serves and the certificate is issued for, so it is the address a user's browser
# actually reaches. It is baked into the images at build time and carried here in the
# release manifest, which is also why no separate runtime setting is needed. DEBUG picks
# the scheme, exactly as the proxy and the dropzone admin pages do.
if [ "${DEBUG}" = "true" ]; then SCHEME="http"; else SCHEME="https"; fi
BASE_URL="${SCHEME}://${SERVER_NAME}"

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
  Grafana:      ${BASE_URL}/${GRAFANA_PATH}/
  PostgreSQL:   host=${SERVER_NAME} port=5432 dbname=postgres user=${SUPERUSER_NAME}
                psql "host=${SERVER_NAME} port=5432 dbname=postgres user=${SUPERUSER_NAME}"

${PORT_SECTION}Follow the combined live log of the whole system:
  journalctl --user -f -u 'main-pod.service' -u 'postgresql.service' \\
    -u 'crudman.service' -u 'sftp.service' -u 'flight.service' -u 'sqlmesh.service' \\
    -u 'grafana.service' -u 'proxy.service'

View persistent log:
  cat \$(podman volume inspect crudman_data -f '{{.Mountpoint}}')/*.log \\
      \$(podman volume inspect sqlmesh_data -f '{{.Mountpoint}}')/*.log \\
      \$(podman volume inspect proxy_data -f '{{.Mountpoint}}')/*.log | sort

Shut the system down:
  systemctl --user stop main-pod.service

Start the system up again:
  systemctl --user start main-pod.service

Run a database backup now:
  podman exec postgresql sh -c 'pg_dumpall -U "\$POSTGRES_USER"' > backup-\$(date +%F).sql

Volume paths (cd into them to inspect data):
  postgresql: \$(podman volume inspect postgresql_data -f '{{.Mountpoint}}')
  grafana:    \$(podman volume inspect grafana_data -f '{{.Mountpoint}}')
  crudman:    \$(podman volume inspect crudman_data -f '{{.Mountpoint}}')
  sqlmesh:    \$(podman volume inspect sqlmesh_data -f '{{.Mountpoint}}')
  proxy:      \$(podman volume inspect proxy_data -f '{{.Mountpoint}}')
  sftp:       \$(podman volume inspect sftp_data -f '{{.Mountpoint}}')
  uploads:    \$(podman volume inspect uploads_data -f '{{.Mountpoint}}')

Edit the runtime configuration:
  ${EDITOR_CMD} \$HOME/.config/${APP_NAME}/runtime.env

Uninstall the system (asks before deleting the data volumes and secrets):
  curl -fsSL ${REPO}/releases/latest/download/uninstall.sh | bash
EOF

cat "$HELP"
echo
echo "This cheat sheet is saved at ${HELP}."
