#!/bin/sh
set -e

# JupyterHub, waiting for the admin panel it authenticates against and for the models
# repository it clones each person's workspace from.
#
# Logs to stdout/stderr only; journald stamps every line. The proxy the hub spawns writes
# its own timestamps, which no setting turns off.

STATE_DIR=/var/lib/app/jupyter
mkdir -p "$STATE_DIR"

# Encrypts the session cookie the hub keeps for each person, which is what lets the spawn
# hook ask crudman for their database credential. From the podman secret rather than a file
# on the volume, and hashed to the 32 bytes JupyterHub wants however long the secret is.
SECRET_FILE="/run/secrets/${SECRET_JUPYTER:-jupyter_secret}"
JUPYTERHUB_CRYPT_KEY="$(sha256sum "$SECRET_FILE" | cut -d' ' -f1)"
export JUPYTERHUB_CRYPT_KEY

# The containers start without ordering, so this one can come up while the admin panel is
# still migrating. Without it the first visitor would be told their session is invalid.
until curl -fsS --max-time 5 -o /dev/null \
      "http://127.0.0.1:8000/${CRUDMAN_PATH:-crudman}/"; do
  echo "Waiting for the administration panel to become available..."
  sleep 2
done

# A workspace is cloned from the models repository, which crudman creates on the volume on
# first start. Only when REPO_MODELS is such a path: a git host is somebody else's to have
# ready, and a spawn against an unreachable one fails with git's own message.
case "${REPO_MODELS}" in
  /*)
    until [ -e "${REPO_MODELS}" ]; do
      echo "Waiting for crudman to create the models repository..."
      sleep 2
    done
    ;;
esac

# Which theme Lab opens in, from DEFAULT_THEME in runtime.env. Written at start rather
# than baked in, because it is the operator's runtime setting, and into overrides.d/ so
# the settings that are part of the release stay in overrides.json untouched. A person
# who picks the other theme in Lab's menu keeps their choice: this is only the default.
# JupyterLab Light rather than a light Material theme: none is installed, and the palette
# in ~/.jupyter/custom/custom.css recolours whichever base theme is underneath anyway.
case "${DEFAULT_THEME:-dark}" in
  light) THEME_NAME="JupyterLab Light" ;;
  *)     THEME_NAME="Material Darker" ;;
esac
OVERRIDES_D="${NOTEBOOK_VENV:-/opt/notebook}/share/jupyter/lab/settings/overrides.d"
mkdir -p "$OVERRIDES_D"
printf '{\n  "@jupyterlab/apputils-extension:themes": {\n    "theme": "%s"\n  }\n}\n' \
  "$THEME_NAME" > "$OVERRIDES_D/theme.json"
# Written as root, read by every person's own server.
chmod a+rX "$OVERRIDES_D" "$OVERRIDES_D/theme.json"

# The config imports the authenticator and the spawner from beside itself.
PYTHONPATH=/etc/jupyterhub
export PYTHONPATH

exec jupyterhub -f /etc/jupyterhub/jupyterhub_config.py
