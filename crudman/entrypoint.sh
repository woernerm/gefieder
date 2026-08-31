#!/bin/sh
set -e

# The one crudman image serves three roles: the admin panel (the default), the dropzones
# SFTP endpoint and the dropzones Arrow Flight endpoint, selected by the quadlets' Exec=
# lines. They share the database wait below.
#
# All three log to stdout/stderr only, and journald rotates and size-caps that stream per
# unit, which a file on the volume did not.
ROLE="${1:-web}"
case "$ROLE" in
  web|sftp|flight) ;;
  *) echo "unknown role: $ROLE" >&2; exit 1 ;;
esac

# The containers in the pod start without ordering, so this one can come up while the
# database is still initializing.
until uv run --project /crudman python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection()" >/dev/null 2>&1; do
  echo "Waiting for the database to become available..."
  sleep 2
done

# PostgreSQL ignores a search_path entry naming no existing schema, so a missing crudman
# schema shifts every CREATE TABLE to public, where this role has no CREATE grant, and the
# migration dies on "permission denied for schema public".
#
# The postgresql entrypoint re-applies gf_0005 on every start, so reaching this point means
# that repair did not happen -- a structural script that failed, or an older image.
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

# The web role owns the migrations and the static files, so the two endpoints wait for it
# rather than racing it.
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

# Migrations are generated and committed during development, so only "migrate" runs.
uv run --project /crudman python manage.py migrate --noinput

# whitenoise's manifest storage needs this before the first request is served.
uv run --project /crudman python manage.py collectstatic --noinput

# Not "manage.py createsuperuser", which fails once the user exists, i.e. on every
# restart. Updating instead also makes rotating the secret rotate the password.
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

# gunicorn ships its access log off; "--access-logfile -" turns it on and points both logs
# at the stream journald captures, so a reported error can be tied to its request.
exec uv run --project /crudman gunicorn -b 0.0.0.0:8000 \
  --access-logfile - --error-logfile - crudman.wsgi:application
