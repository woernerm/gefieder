#!/bin/sh
# Uninstaller for a deployment made by install.sh.
#
# Run it from a checkout, or straight from a release without one:
#
#   curl -fsSL ${REPO}/releases/latest/download/uninstall.sh | bash
#
# It removes what install.sh installed: the systemd units and the pod they run, the
# loaded images, the server-statistics collector, the helpfile and the io-delegation
# drop-in. The data volumes and the podman secrets are the two things that cannot be
# recreated, so it asks about each before touching them.
#
# Nothing here is hardcoded from install.sh's lists. The installed files are the
# inventory: the quadlet directory names the units and (through VolumeName=) the volumes,
# the *.container files name the images, and manifest.env recorded APP_NAME. A service
# added to install.sh is therefore removed by this script without a matching edit here.
set -e

QUADLET_DIR="$HOME/.config/containers/systemd"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

step() { echo; echo "==> $*"; }

# Prompts read from the terminal rather than stdin, because the documented way to run
# this is piped from curl -- there stdin is the script itself, and `read` would consume
# it. With no terminal at all there is no safe default for "delete the data or not", so
# the run stops instead of guessing. The test opens /dev/tty rather than checking its
# permissions: the device node exists and looks readable even in a session that has no
# controlling terminal, where opening it is what actually fails.
if (: >/dev/tty) 2>/dev/null; then
  ask() { printf '%s' "$1" >/dev/tty; read -r REPLY </dev/tty; }
else
  ask() {
    echo >&2
    echo "Cannot ask '$1' -- no terminal is attached." >&2
    echo "The uninstaller asks before deleting data, so it stops here. Run it from a" >&2
    echo "terminal; when piping from curl, keep the terminal attached:" >&2
    echo "  curl -fsSL <url>/uninstall.sh -o uninstall.sh && sh uninstall.sh" >&2
    exit 1
  }
fi

command -v podman >/dev/null || { echo "podman is not installed." >&2; exit 1; }

# --- discover the deployment ----------------------------------------------------------
# APP_NAME names the pod, the config directory and the helpfile. install.sh took it from
# the release manifest and wrote the config directory named after it; recover it from
# there so this script needs no release download. The glob resolves to the single
# directory holding a serverstats/ subdirectory, which is what install.sh creates.
step "Looking for an installed deployment"
CONFIG_HOME="$HOME/.config"
APP_NAME=""
for d in "$CONFIG_HOME"/*/serverstats; do
  [ -d "$d" ] || continue
  APP_NAME="$(basename "$(dirname "$d")")"
done

# The pod name is the fallback when the config directory is already gone: quadlet names
# the generated unit after main.pod regardless of PodName, so ask systemd's generator
# output instead -- the pod is whatever main-pod.service runs.
if [ -z "$APP_NAME" ] && [ -f "$QUADLET_DIR/main.pod" ]; then
  APP_NAME="$(sed -n 's/^PodName=//p' "$QUADLET_DIR/main.pod" | head -n 1)"
fi

echo "  application name: ${APP_NAME:-unknown}"

# The units are the quadlet files themselves: <name>.container becomes <name>.service.
# Reading the directory rather than a list means units from a newer release are stopped
# too, even ones this script predates.
UNITS=""
for f in "$QUADLET_DIR"/*.container; do
  [ -e "$f" ] || continue
  UNITS="$UNITS $(basename "$f" .container).service"
done

# The volumes come from the VolumeName= line of each *.volume quadlet -- the same line
# podman itself uses, so the names always match what was created.
VOLUMES=""
for f in "$QUADLET_DIR"/*.volume; do
  [ -e "$f" ] || continue
  name="$(sed -n 's/^VolumeName=//p' "$f" | head -n 1)"
  VOLUMES="$VOLUMES ${name:-$(basename "$f" .volume)}"
done

# The images are the Image= lines of the container quadlets, deduplicated: several
# containers (crudman, sftp, flight) share one image.
IMAGES="$(sed -n 's/^Image=//p' "$QUADLET_DIR"/*.container 2>/dev/null | sort -u)"

# Leftovers outlive the unit files: a previous run may have kept the volumes or secrets,
# and the config directory alone is enough to recover APP_NAME. So "nothing to uninstall"
# means none of them are present -- checking only the quadlet directory would march
# through the whole removal with nothing to remove, and checking only APP_NAME would miss
# a deployment whose config directory is already gone.
LEFTOVERS=""
for s in django_secret_key crudman_password sqlmesh_password grafana_password superuser_password; do
  podman secret exists "$s" 2>/dev/null && LEFTOVERS="found"
done
for vol in $VOLUMES; do
  podman volume exists "$vol" 2>/dev/null && LEFTOVERS="found"
done
if [ -n "$APP_NAME" ]; then
  podman pod exists "$APP_NAME" 2>/dev/null && LEFTOVERS="found"
  [ -d "$CONFIG_HOME/$APP_NAME" ] && LEFTOVERS="found"
fi
if [ -z "$UNITS" ] && [ -z "$VOLUMES" ] && [ -z "$LEFTOVERS" ] && [ ! -f "$QUADLET_DIR/main.pod" ]; then
  echo "No deployment found in ${QUADLET_DIR}; nothing to uninstall."
  exit 0
fi

# --- stop the services ----------------------------------------------------------------
# Stop the units before removing anything they hold open. main-pod.service goes last:
# stopping it first would make systemd restart the containers it still wants running.
step "Stopping the services"
for u in $UNITS; do systemctl --user stop "$u" >/dev/null 2>&1 || true; done
systemctl --user disable --now server-stats.timer >/dev/null 2>&1 || true
systemctl --user stop server-stats.service >/dev/null 2>&1 || true
systemctl --user stop main-pod.service >/dev/null 2>&1 || true
echo "  $(echo $UNITS | wc -w) service units stopped"

# --- remove the pod and its containers ------------------------------------------------
# The quadlet units are gone below, but the pod they generated outlives them; remove it
# explicitly so a later install starts from a clean pod.
step "Removing the pod and its containers"
if [ -n "$APP_NAME" ]; then
  podman pod rm -f "$APP_NAME" >/dev/null 2>&1 || true
fi
# Containers a partial or interrupted install left outside the pod, named after their
# quadlets. Harmless when the pod already took them.
for u in $UNITS; do
  podman rm -f "${u%.service}" >/dev/null 2>&1 || true
done
echo "  done"

# --- remove the unit files ------------------------------------------------------------
step "Removing the unit files"
rm -f "$QUADLET_DIR"/*.pod "$QUADLET_DIR"/*.container "$QUADLET_DIR"/*.volume
rm -f "$SYSTEMD_USER_DIR/server-stats.service" "$SYSTEMD_USER_DIR/server-stats.timer"
systemctl --user daemon-reload >/dev/null 2>&1 || true
echo "  removed from ${QUADLET_DIR} and ${SYSTEMD_USER_DIR}"

# --- remove the loaded images ---------------------------------------------------------
step "Removing the images"
img_n=0
for img in $IMAGES; do
  podman rmi -f "$img" >/dev/null 2>&1 && img_n=$((img_n + 1)) || true
done
echo "  $img_n images removed"

# --- data volumes: ask, they hold everything the deployment ever collected -------------
# Listed by name with their size, because "the database" is more abstract than a path the
# operator can go and look at before answering.
step "Data volumes"
if [ -n "$VOLUMES" ]; then
  for vol in $VOLUMES; do
    if podman volume exists "$vol" 2>/dev/null; then
      echo "  ${vol}  ($(podman volume inspect "$vol" -f '{{.Mountpoint}}' 2>/dev/null))"
    fi
  done
  echo
  echo "  Removing these deletes all data in them: the database, the dropzone uploads,"
  echo "  the Grafana dashboards and the persistent logs. This cannot be undone."
  ask "Delete the data volumes? [y/N] "
  case "$REPLY" in
    y|Y|yes|YES|Yes)
      podman volume rm -f $VOLUMES >/dev/null 2>&1 || true
      echo "  volumes deleted"
      ;;
    *)
      echo "  volumes kept; a later install reuses them"
      ;;
  esac
else
  echo "  none found"
fi

# --- podman secrets: ask, the superuser password is not recoverable --------------------
# Only the secrets install.sh creates are offered; other secrets on the machine are not
# this deployment's business. Keeping them lets a reinstall skip the password prompt.
step "Secrets"
SECRETS="django_secret_key crudman_password sqlmesh_password grafana_password superuser_password"
FOUND=""
for s in $SECRETS; do
  podman secret exists "$s" 2>/dev/null && FOUND="$FOUND $s"
done
if [ -n "$FOUND" ]; then
  echo " $FOUND" | tr ' ' '\n' | sed '/^$/d;s/^/  /'
  echo
  ask "Delete these secrets? [y/N] "
  case "$REPLY" in
    y|Y|yes|YES|Yes)
      podman secret rm $FOUND >/dev/null 2>&1 || true
      echo "  secrets deleted"
      ;;
    *)
      echo "  secrets kept; a later install reuses them"
      ;;
  esac
else
  echo "  none found"
fi

# --- configuration, collector and helpfile ---------------------------------------------
# runtime.env is the operator's own tuning, so the config directory is a separate question
# from the volumes above -- keeping it makes a reinstall come back configured.
step "Configuration and collector"
if [ -n "$APP_NAME" ] && [ -d "$CONFIG_HOME/$APP_NAME" ]; then
  echo "  ${CONFIG_HOME}/${APP_NAME} (runtime.env and the statistics collector)"
  ask "Delete the configuration directory? [y/N] "
  case "$REPLY" in
    y|Y|yes|YES|Yes)
      rm -rf "${CONFIG_HOME:?}/${APP_NAME:?}"
      echo "  configuration deleted"
      ;;
    *)
      echo "  configuration kept"
      ;;
  esac
else
  echo "  none found"
fi

# The helpfile is named after the lower-cased application name, so it has to be lower-cased
# here too: APP_NAME is recovered from the config directory, which keeps its original case.
if [ -n "$APP_NAME" ]; then
  rm -f "$HOME/$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]')-help.txt"
fi

# --- the io-delegation drop-in ---------------------------------------------------------
# install.sh wrote this with sudo, so removing it needs sudo too. It is a system file that
# affects every user slice, so only remove it without prompting for a password; an
# operator without sudo here is told what to run instead of being asked for credentials.
if [ -n "$APP_NAME" ]; then
  IO_DROPIN=/etc/systemd/system/user@.service.d/10-${APP_NAME}-delegate-io.conf
  if [ -f "$IO_DROPIN" ]; then
    step "Removing the io cgroup delegation"
    if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
      sudo rm -f "$IO_DROPIN" && sudo systemctl daemon-reload 2>/dev/null || true
      echo "  removed ${IO_DROPIN}"
    else
      echo "  ${IO_DROPIN} needs root to remove; run:"
      echo "    sudo rm -f ${IO_DROPIN} && sudo systemctl daemon-reload"
    fi
  fi
fi

# Lingering is left enabled on purpose: it is a per-user setting that other services may
# rely on, and install.sh is not necessarily what turned it on.
step "Uninstall complete"
echo "Lingering was left enabled for '$(id -un)'. Turn it off with:"
echo "  loginctl disable-linger $(id -un)"
