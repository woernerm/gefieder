# Shared definitions for the deploy step scripts. Sourced, not executed.
#
# The deployment is split into separate scripts (update, backup, deploy, rollback) so
# the GitHub workflow can run them one by one and show which step passes or fails. They
# run in separate SSH sessions, so shared state (the backup location) lives on disk at
# a fixed path rather than in a variable.

# Load the configured settings (SUPERUSER_NAME, APP_NAME, ...).
set -a
. ./.env
set +a

# Fixed backup location so backup.sh and rollback.sh agree without passing state.
BACKUP_DIR="/var/tmp/gefieder-deploy-backup"
DB_DUMP="$BACKUP_DIR/db.sql"
PREV_TAG="previous"

# The images the production stack builds, named "<project>_<service>" by podman-compose,
# where the project name is APP_NAME.
IMAGES="${APP_NAME}_crudman ${APP_NAME}_sqlmesh ${APP_NAME}_postgresql"

log() { echo ">>> $*"; }
