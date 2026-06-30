#!/bin/sh
# Gefieder installer for a GitHub release.
#
# Run it straight from a release without a checkout:
#
#   curl -fsSL https://github.com/woernerm/gefieder/releases/latest/download/install.sh | bash
#
# It downloads each release asset with its own curl command, loads the image tarballs
# into rootless podman, installs the rendered quadlets, creates the machine secrets, and
# prints a cheat sheet. The values baked into the images at build time (APP_NAME, paths,
# the superuser name) are recorded in the release's manifest.env, which is sourced below.
set -e

# --- where the release lives ----------------------------------------------------------
# Default to the latest release of the upstream repository; override REPO/TAG to install
# a fork or a pinned version, e.g. REPO=myorg/gefieder TAG=v1.2.0 ./install.sh
REPO="${REPO:-woernerm/gefieder}"
TAG="${TAG:-latest}"
if [ "$TAG" = "latest" ]; then
  BASE="https://github.com/${REPO}/releases/latest/download"
else
  BASE="https://github.com/${REPO}/releases/download/${TAG}"
fi

QUADLET_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/containers/systemd"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The images built and saved by the workflow, and the unit files it ships. Keep these in
# sync with the workflow's matrix and the quadlets/ directory.
IMAGES="postgresql crudman sqlmesh proxy grafana"
QUADLETS="main.pod postgresql.container crudman.container sqlmesh.container \
  grafana.container proxy.container postgresql_data.volume grafana_data.volume \
  crudman_data.volume sqlmesh_data.volume proxy_data.volume"

# --- preflight: rootless podman needs subuid/subgid mappings --------------------------
# Without a range mapped for this user, rootless containers cannot start. Fail early with
# a fixable message instead of a confusing runtime error later.
if ! grep -q "^$(id -un):" /etc/subuid || ! grep -q "^$(id -un):" /etc/subgid; then
  echo "No subuid/subgid mappings for '$(id -un)'. Ask an admin to run:" >&2
  echo "  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $(id -un)" >&2
  exit 1
fi

command -v podman >/dev/null || { echo "podman is not installed." >&2; exit 1; }

# --- images: download each tarball with its own curl, then load it --------------------
echo "Downloading the release from ${BASE} ..."
curl -fsSL "${BASE}/manifest.env" -o "${WORK}/manifest.env"
. "${WORK}/manifest.env"   # APP_NAME, SUPERUSER_NAME, CRUDMAN_PATH, GRAFANA_PATH, ...

for img in $IMAGES; do
  curl -fsSL "${BASE}/${img}.tar" -o "${WORK}/${img}.tar"
  podman load -i "${WORK}/${img}.tar"
done

# --- quadlets: download each unit file with its own curl, then install it --------------
mkdir -p "$QUADLET_DIR"
for q in $QUADLETS; do
  curl -fsSL "${BASE}/${q}" -o "${WORK}/${q}"
  cp "${WORK}/${q}" "$QUADLET_DIR/$q"
done

# --- server-statistics collector: host-side timer, collector and default config --------
# The collector runs on the host (not in a container) so it can read the pod's cgroup
# counters, disk IOPS and network egress. Its systemd user units live with the other user
# units; the script and the runtime config live under ~/.config/<APP_NAME>/.
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
for vol in postgresql_data grafana_data crudman_data sqlmesh_data proxy_data; do
  podman volume exists "$vol" || podman volume create "$vol" >/dev/null
done

# --- machine secrets ------------------------------------------------------------------
# One secret per non-human credential, generated locally with openssl. Human logins (the
# superuser) are NOT created here: the superuser password is prompted once below so it
# never lands in a file or the shell history. A secret that already exists is left as is.
create_secret() {  # name, value-producing command
  podman secret exists "$1" 2>/dev/null || printf '%s' "$2" | podman secret create "$1" - >/dev/null
}
create_secret django_secret_key "$(openssl rand -hex 32)"
create_secret crudman_password  "$(openssl rand -hex 32)"
create_secret sqlmesh_password  "$(openssl rand -hex 32)"
create_secret grafana_password  "$(openssl rand -hex 32)"

if ! podman secret exists superuser_password 2>/dev/null; then
  printf 'Set the superuser (admin) password: '
  stty -echo; read -r SU_PW; stty echo; echo
  printf '%s' "$SU_PW" | podman secret create superuser_password - >/dev/null
  unset SU_PW
fi

# --- delegate the io cgroup controller so the collector can read disk IOPS/throughput --
# systemd delegates cpu/memory/pids to a user slice by default but withholds io, so the
# pod's io.stat (the source of the disk IOPS and read/write-speed figures) is absent
# without this. The drop-in needs root; attempt it with sudo and carry on if unavailable
# (the collector then records those two metrics as NULL, the others still work). A kernel
# that does not expose per-cgroup io at all (some WSL2 builds) is unaffected by this.
IO_DROPIN=/etc/systemd/system/user@.service.d/10-gefieder-delegate-io.conf
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
loginctl enable-linger "$(id -un)" 2>/dev/null || true
systemctl --user daemon-reload

# Enable the server-statistics timer so sampling starts (and resumes after a reboot)
# without a manual step. enable --now both enables it for future boots and starts it now.
systemctl --user enable --now server-stats.timer 2>/dev/null || true

# --- helpfile + cheat sheet -----------------------------------------------------------
# Store the cheat sheet in the user's home so it is available later, and print it now.
HELP="$HOME/${APP_NAME}-help.txt"
EDITOR_CMD="${EDITOR:-${VISUAL:-nano}}"
PG_VOL="postgresql_data"
GF_VOL="grafana_data"

cat > "$HELP" <<EOF
Gefieder control cheat sheet
============================

Start the system now:
  systemctl --user start main-pod.service

Run a database backup now:
  podman exec postgresql sh -c 'pg_dumpall -U "\$POSTGRES_USER"' > backup-\$(date +%F).sql

Follow the combined live log of the whole system:
  journalctl --user -f -u 'main-pod.service' -u 'postgresql.service' \\
    -u 'crudman.service' -u 'sqlmesh.service' -u 'grafana.service' -u 'proxy.service'

Follow the live log of a single component:
  journalctl --user -f -u postgresql.service     # or crudman / sqlmesh / grafana / proxy

Volume paths (cd into them to inspect data):
  postgresql: \$(podman volume inspect ${PG_VOL} -f '{{.Mountpoint}}')
  grafana:    \$(podman volume inspect ${GF_VOL} -f '{{.Mountpoint}}')

Edit the runtime configuration in your default editor:
  ${EDITOR_CMD} \$HOME/.config/${APP_NAME}/runtime.env

Server-statistics sampling (resource usage for sizing, query stats for indexing):
  systemctl --user status server-stats.timer        # is sampling active?
  systemctl --user start server-stats.service       # take a sample right now
  journalctl --user -f -u server-stats.service      # follow the sampler
  The data lives in the '${SERVER_STATS_SCHEMA:-server_stats}' schema of the database.

View the persistent log of a component (survives a crash, unlike journald):
  cat \$(podman volume inspect ${PG_VOL} -f '{{.Mountpoint}}')/log/postgresql-*.log
  cat \$(podman volume inspect ${GF_VOL} -f '{{.Mountpoint}}')/log/grafana.log
  cat \$(podman volume inspect crudman_data -f '{{.Mountpoint}}')/crudman.log
  cat \$(podman volume inspect sqlmesh_data -f '{{.Mountpoint}}')/sqlmesh.log
  cat \$(podman volume inspect proxy_data   -f '{{.Mountpoint}}')/proxy.log
EOF

cat "$HELP"
echo
echo "This cheat sheet is saved at ${HELP}."
