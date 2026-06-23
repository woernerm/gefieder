# Allow unsigned and community extensions for all future sessions. However, this only
# takes effect after restarting PostgreSQL or reloading the configuration.
echo "duckdb.allow_unsigned_extensions = true" >> "$PGDATA/postgresql.conf"
echo "duckdb.allow_community_extensions = true" >> "$PGDATA/postgresql.conf"

# Persist the server log into the data volume (under $PGDATA/log) instead of only the
# container's stdout, so a crash leaves a log on disk to diagnose it from. The files are
# written by the postgres user, which the rootless container maps back to the podman
# user, so they need no chown. log_min_messages stays at the server default.
echo "logging_collector = on" >> "$PGDATA/postgresql.conf"
echo "log_directory = 'log'" >> "$PGDATA/postgresql.conf"
echo "log_filename = 'postgresql-%Y-%m-%d.log'" >> "$PGDATA/postgresql.conf"

# Reload PostgreSQL configuration to apply the new settings.
pg_ctl reload -D "$PGDATA" || true  # || true ignores error if not running yet