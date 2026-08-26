#!/bin/sh
set -e

# The one crudman image serves three roles: the admin panel (the default), the dropzones
# SFTP endpoint ("sftp", used by sftp.container) and the dropzones Arrow Flight endpoint
# ("flight", used by flight.container). They share the database wait below.
#
# All three log to stdout/stderr only; journald captures the stream per unit and is what
# survives a crash, a container replacement and a restart. It rotates and size-caps the
# log on its own, which a file on the volume did not.
ROLE="${1:-web}"
case "$ROLE" in
  web|sftp|flight) ;;
  *) echo "unknown role: $ROLE" >&2; exit 1 ;;
esac

# Wait until PostgreSQL accepts connections, because the containers in the pod start
# without ordering and this one can come up while the database is still initializing.
until uv run --project /crudman python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection()" >/dev/null 2>&1; do
  echo "Waiting for the database to become available..."
  sleep 2
done

# Fail loudly if the crudman schema is gone. PostgreSQL silently ignores a search_path
# entry that names no existing schema, so a missing crudman schema does not raise here --
# it quietly shifts every CREATE TABLE to the next entry, public, where this role has no
# CREATE grant. The migration then dies on "permission denied for schema public", naming a
# schema nobody configured.
#
# The postgresql entrypoint re-applies gf_0005 on every start and normally recreates the
# schema before this runs, so reaching this point means that repair did not happen -- a
# structural script that failed, or a database still starting from an older image. Say so
# rather than letting the misleading permission error stand.
if ! uv run --project /crudman python manage.py shell -c "
import sys
from django.db import connection

with connection.cursor() as cursor:
    cursor.execute(\"SELECT to_regnamespace('crudman') IS NOT NULL\")
    sys.exit(0 if cursor.fetchone()[0] else 1)
"; then
  echo "The crudman schema does not exist in the database." >&2
  echo "The postgresql container recreates it from gf_0005_create_schemas.sql each time" >&2
  echo "it starts, so check its log for a failed structural init script, then restart it." >&2
  exit 1
fi

# The SFTP and Arrow Flight endpoints only serve; the web role owns the migrations and
# the static files, so wait here until it has applied the migrations rather than racing
# it.
if [ "$ROLE" = "sftp" ] || [ "$ROLE" = "flight" ]; then
  until uv run --project /crudman python manage.py migrate --check >/dev/null 2>&1; do
    echo "Waiting for crudman to apply the database migrations..."
    sleep 2
  done
  if [ "$ROLE" = "sftp" ]; then
    exec uv run --project /crudman python manage.py sftpserver
  fi
  exec uv run --project /crudman python manage.py flightserver
fi

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
user.set_password(
    Path('/run/secrets', os.environ.get('SECRET_SUPERUSER_PASSWORD', 'superuser_password'))
    .read_text().strip()
)
user.save()
"

exec uv run --project /crudman gunicorn -b 0.0.0.0:8000 crudman.wsgi:application
