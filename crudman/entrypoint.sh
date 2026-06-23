#!/bin/sh
set -e

# Persist everything this script and the server print into the mounted log volume while
# still echoing to stdout, so "podman logs"/journald keep working and a crash also leaves
# its cause on disk. Process substitution is a bashism unavailable in this dash /bin/sh,
# so on first entry the script re-runs itself with stdout+stderr piped through tee -a
# (which appends, so logs survive restarts); GEFIEDER_LOGGING guards against looping. A
# pipeline cannot be exec'd and its status would be tee's, so the real exit status is
# captured via a status file and re-raised, keeping the container's exit code (and thus
# Restart=) honest on a crash. The volume is owned by the rootless podman user already.
LOG_DIR=/var/log/gefieder
if [ -z "$GEFIEDER_LOGGING" ]; then
  mkdir -p "$LOG_DIR"
  export GEFIEDER_LOGGING=1
  STATUS_FILE="$(mktemp)"
  { "$0" "$@"; echo $? > "$STATUS_FILE"; } 2>&1 | tee -a "$LOG_DIR/crudman.log" || true
  status="$(cat "$STATUS_FILE" 2>/dev/null || echo 1)"; rm -f "$STATUS_FILE"
  exit "$status"
fi

# Wait until PostgreSQL accepts connections, because the containers in the pod start
# without ordering and this one can come up while the database is still initializing.
until uv run --project /crudman python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection()" >/dev/null 2>&1; do
  echo "Waiting for the database to become available..."
  sleep 2
done

# Apply the committed database migrations before starting the application server.
# Migrations are generated and committed during development, not authored here against
# live data, so only "migrate" runs.
uv run --project /crudman python manage.py migrate --noinput

# Collect the static files for whitenoise. With DEBUG disabled, the manifest static
# files storage requires this to have run before the first request is served.
uv run --project /crudman python manage.py collectstatic --noinput

# Create or update the Django superuser with the password from the mounted secret.
# This is used instead of "manage.py createsuperuser" because createsuperuser fails
# if the user already exists, i.e. on every container restart. Updating the existing
# user instead also means that rotating the secret rotates the superuser password on
# the next restart.
uv run --project /crudman python manage.py shell -c "
import os
from pathlib import Path
from django.contrib.auth import get_user_model

user, _ = get_user_model().objects.get_or_create(username=os.environ.get('SUPERUSER_NAME', 'admin'))
user.is_staff = user.is_superuser = True
user.email = os.environ.get('SUPERUSER_EMAIL', '')
user.set_password(Path('/run/secrets/superuser_password').read_text().strip())
user.save()
"

exec uv run --project /crudman gunicorn -b 0.0.0.0:8000 crudman.wsgi:application
