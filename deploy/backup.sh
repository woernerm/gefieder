#!/bin/sh
# Step 2: back up the current database and images so the deployment can be rolled back.
# Dumps the database with pg_dumpall and tags the current images as ":previous".
set -e

. ./deploy/_common.sh

rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

# --clean --if-exists makes the dump drop each object before recreating it, so the
# rollback restore overwrites the live database cleanly instead of appending to it.
log "Backing up the database with pg_dumpall..."
podman exec postgresql pg_dumpall -U "${SUPERUSER_NAME:-admin}" --clean --if-exists > "$DB_DUMP"

log "Tagging the current images as :$PREV_TAG..."
for img in $IMAGES; do
  # Only tag if the image exists (the first deploy may not have all of them yet).
  if podman image exists "localhost/${img}:latest"; then
    podman tag "localhost/${img}:latest" "localhost/${img}:${PREV_TAG}"
  fi
done

log "Backup complete in $BACKUP_DIR."
