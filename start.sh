#!/bin/sh

set -eu

# Start PostgreSQL using the base image entrypoint behavior.
/usr/local/bin/docker-entrypoint.sh postgres &
PG_PID=$!

trap 'kill "$PG_PID" 2>/dev/null || true' EXIT

# Wait until PostgreSQL accepts connections before starting the application.
for _ in $(seq 1 60); do
  if pg_isready -h localhost -p 5432 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Start the Django application with Gunicorn.
exec uv run --project /crudman gunicorn -b 0.0.0.0:8000 crudman.wsgi:application
