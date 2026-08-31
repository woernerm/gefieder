#!/bin/sh
# Uninstaller for a deployment made by install.sh.
#
# Run it from a checkout, or straight from a release without one:
#
#   curl -fsSL ${REPO}/releases/latest/download/uninstall.sh | bash
#
# It removes what install.sh installed: the systemd units and the pod they run, the loaded
# images, the collector, the helpfile and the io-delegation drop-in. The data volumes and
# the podman secrets cannot be recreated, so it asks about each first.
#
# Nothing here repeats install.sh's lists. The installed files are the inventory: the
# quadlet directory names the units and, through VolumeName=, the volumes; the *.container
# files name the images. A service added to install.sh needs no edit here.
set -e

QUADLET_DIR="$HOME/.config/containers/systemd"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"

step() { echo; echo "==> $*"; }

# Prompts read from the terminal rather than stdin: piped from curl, stdin is the script
# itself and `read` would consume it. With no terminal there is no safe default for
# "delete the data or not", so the run stops. The test opens /dev/tty rather than checking
# its permissions, the node looking readable even where opening it fails.
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
# APP_NAME names the pod, the config directory and the helpfile. Recovered from the config
# directory install.sh named after it, so this script needs no release download: the glob
# resolves to the one directory holding a serverstats/ subdirectory.
step "Looking for an installed deployment"
CONFIG_HOME="$HOME/.config"
APP_NAME=""
for d in "$CONFIG_HOME"/*/serverstats; do
  [ -d "$d" ] || continue
  APP_NAME="$(basename "$(dirname "$d")")"
done

# The fallback when the config directory is gone. Quadlet names the generated unit after
# main.pod regardless of PodName, so the pod is whatever main-pod.service runs.
if [ -z "$APP_NAME" ] && [ -f "$QUADLET_DIR/main.pod" ]; then
  APP_NAME="$(sed -n 's/^PodName=//p' "$QUADLET_DIR/main.pod" | head -n 1)"
fi

echo "  application name: ${APP_NAME:-unknown}"

# <name>.container becomes <name>.service. Reading the directory rather than a list stops
# units from a newer release too.
UNITS=""
for f in "$QUADLET_DIR"/*.container; do
  [ -e "$f" ] || continue
  UNITS="$UNITS $(basename "$f" .container).service"
done

# From the VolumeName= line of each *.volume quadlet, the line podman itself uses.
VOLUMES=""
for f in "$QUADLET_DIR"/*.volume; do
  [ -e "$f" ] || continue
  name="$(sed -n 's/^VolumeName=//p' "$f" | head -n 1)"
  VOLUMES="$VOLUMES ${name:-$(basename "$f" .volume)}"
done

# The Image= lines, deduplicated: crudman, sftp and flight share one image.
IMAGES="$(sed -n 's/^Image=//p' "$QUADLET_DIR"/*.container 2>/dev/null | sort -u)"

# The Secret= lines, likewise deduplicated. Their names were rendered in at build time, so
# reading them back is the only way to learn what a deployment that renamed one created.
SECRETS="$(sed -n 's/^Secret=//p' "$QUADLET_DIR"/*.container 2>/dev/null | sort -u)"

# Leftovers outlive the unit files: a previous run may have kept the volumes or secrets.
# So "nothing to uninstall" means none of them are present; the quadlet directory alone
# would march through a removal with nothing to remove.
LEFTOVERS=""
for s in $SECRETS; do
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
# In one go, as run-tests.sh tears its stack down. Quadlet binds every container unit to
# main-pod.service, so stopping the pod takes them down together and Restart=always does
# not fire. Unit by unit would stop the database first and leave the rest failing their
# healthcheck, which Restart=always *does* answer. The per-unit stops below only confirm.
step "Stopping the services"
systemctl --user stop main-pod.service >/dev/null 2>&1 || true
for u in $UNITS; do systemctl --user stop "$u" >/dev/null 2>&1 || true; done
systemctl --user disable --now server-stats.timer >/dev/null 2>&1 || true
systemctl --user stop server-stats.service >/dev/null 2>&1 || true
echo "  $(echo $UNITS | wc -w) service units stopped"

# --- remove the pod and its containers ------------------------------------------------
# The pod outlives the units removed below, so a later install would inherit it.
step "Removing the pod and its containers"
if [ -n "$APP_NAME" ]; then
  podman pod rm -f "$APP_NAME" >/dev/null 2>&1 || true
fi
# Containers an interrupted install left outside the pod, named after their quadlets.
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
# By name and size: "the database" is more abstract than a path to go and look at.
step "Data volumes"
if [ -n "$VOLUMES" ]; then
  for vol in $VOLUMES; do
    if podman volume exists "$vol" 2>/dev/null; then
      echo "  ${vol}  ($(podman volume inspect "$vol" -f '{{.Mountpoint}}' 2>/dev/null))"
    fi
  done
  echo
  echo "  Removing these deletes all data in them: the database, the dropzone uploads,"
  echo "  the Grafana dashboards and the SFTP host key. This cannot be undone."
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
# Only the secrets the quadlets named; others on the machine are not this deployment's
# business. Keeping them lets a reinstall skip the password prompt. A run whose quadlets
# are gone finds none and says so rather than guessing at names.
step "Secrets"
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
# runtime.env is the operator's own tuning, so keeping it makes a reinstall come back
# configured -- a separate question from the volumes above.
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

# The helpfile name is lower-cased; APP_NAME comes from the config directory, which keeps
# its original case.
if [ -n "$APP_NAME" ]; then
  rm -f "$HOME/$(printf '%s' "$APP_NAME" | tr '[:upper:]' '[:lower:]')-help.txt"
fi

# --- the io-delegation drop-in ---------------------------------------------------------
# Written with sudo, so removing it needs sudo. It affects every user slice, so an
# operator without passwordless sudo is told what to run rather than asked for a password.
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

# Lingering stays: a per-user setting other services may rely on, and install.sh is not
# necessarily what turned it on.
step "Uninstall complete"
echo "Lingering was left enabled for '$(id -un)'. Turn it off with:"
echo "  loginctl disable-linger $(id -un)"
