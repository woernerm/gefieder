#!/bin/sh
# Rollback: restore the previous images and database from the backup. Run only when a
# later step (the smoke test) failed.
set -e

. ./deploy/_common.sh

if [ ! -f "$DB_DUMP" ]; then
  echo "No backup found at $DB_DUMP; cannot roll back." >&2
  exit 1
fi

echo "Rolling back to the previous version..." >&2

log "Restoring the previous images..."
for img in $IMAGES; do
  if podman image exists "localhost/${img}:${PREV_TAG}"; then
    podman tag "localhost/${img}:${PREV_TAG}" "localhost/${img}:latest"
  fi
done

log "Recreating the containers from the restored images..."
podman-compose up -d --force-recreate

log "Waiting for the database to accept connections..."
until podman exec postgresql pg_isready -U "${SUPERUSER_NAME:-admin}" -d postgres >/dev/null 2>&1; do
  sleep 2
done

# The dump was taken with --clean --if-exists, so it drops and recreates the data. A
# handful of "role ... already exists / cannot be dropped" notices are expected and
# harmless: the container's initdb recreated the login roles on boot, and they cannot
# be dropped while objects depend on them. These do not affect the restored data.
log "Restoring the database from the pre-deploy dump..."
podman exec -i postgresql psql -U "${SUPERUSER_NAME:-admin}" -d postgres < "$DB_DUMP"

echo "Rollback complete. The previous version is running." >&2
