# Allow unsigned and community extensions for all future sessions. However, this only 
# takes effect after restarting PostgreSQL or reloading the configuration.
echo "duckdb.allow_unsigned_extensions = true" >> "$PGDATA/postgresql.conf"
echo "duckdb.allow_community_extensions = true" >> "$PGDATA/postgresql.conf"

# Reload PostgreSQL configuration to apply the new settings.
pg_ctl reload -D "$PGDATA" || true  # || true ignores error if not running yet

# Create the dedicated Crudman database user and schema from the mounted secret.
crudman_password="$(cat /run/secrets/crudman_password 2>/dev/null || true)"
if [ -n "$crudman_password" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v crudman_password="$crudman_password" \
    -f /docker-entrypoint-initdb.d/gf_0004_create_users.sql \
    -f /docker-entrypoint-initdb.d/gf_0005_create_schemas.sql
fi