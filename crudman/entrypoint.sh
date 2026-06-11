#!/bin/sh
set -e

# Wait until PostgreSQL accepts connections. podman-compose does not honor the
# "condition: service_healthy" dependency in compose.yaml, so on a fresh boot this
# container can start while the database is still initializing.
until uv run --project /crudman python manage.py shell -c \
  "from django.db import connection; connection.ensure_connection()" >/dev/null 2>&1; do
  echo "Waiting for the database to become available..."
  sleep 2
done

# Create and apply database migrations before starting the application server.
uv run --project /crudman python manage.py makemigrations --noinput
uv run --project /crudman python manage.py migrate --noinput

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
