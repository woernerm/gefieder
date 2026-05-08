# Allow unsigned and community extensions for all future sessions. However, this only 
# takes effect after restarting PostgreSQL or reloading the configuration.
echo "duckdb.allow_unsigned_extensions = true" >> "$PGDATA/postgresql.conf"
echo "duckdb.allow_community_extensions = true" >> "$PGDATA/postgresql.conf"

# Reload PostgreSQL configuration to apply the new settings.
pg_ctl reload -D "$PGDATA" || true  # || true ignores error if not running yet