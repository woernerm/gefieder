# Allow unsigned and community extensions for all future sessions. However, this only
# takes effect after restarting PostgreSQL or reloading the configuration.
echo "duckdb.allow_unsigned_extensions = true" >> "$PGDATA/postgresql.conf"
echo "duckdb.allow_community_extensions = true" >> "$PGDATA/postgresql.conf"

# Load pg_stat_statements so the server records per-query execution statistics (calls,
# total time, rows, shared-buffer hits/reads). The collector snapshots these to find the
# queries worth optimising, e.g. a frequent sequential scan that an index would fix.
# The base image hard-sets shared_preload_libraries='pg_duckdb' in postgresql.conf; this
# later line wins, so it must re-list pg_duckdb to keep DuckDB loaded. Both the preload
# and pg_stat_statements only take effect after the server restart that initdb performs
# before opening for connections, so the reload below does not yet activate them.
echo "shared_preload_libraries = 'pg_duckdb,pg_stat_statements'" >> "$PGDATA/postgresql.conf"
# Track statements nested inside functions and procedures too (the default 'top' misses
# them), so SQLMesh models wrapped in calls still show up individually.
echo "pg_stat_statements.track = all" >> "$PGDATA/postgresql.conf"

# Persist the server log into the data volume (under $PGDATA/log) instead of only the
# container's stdout, so a crash leaves a log on disk to diagnose it from. The files are
# written by the postgres user, which the rootless container maps back to the podman
# user, so they need no chown. log_min_messages stays at the server default.
echo "logging_collector = on" >> "$PGDATA/postgresql.conf"
echo "log_directory = 'log'" >> "$PGDATA/postgresql.conf"
echo "log_filename = 'postgresql-%Y-%m-%d.log'" >> "$PGDATA/postgresql.conf"

# Reload PostgreSQL configuration to apply the new settings.
pg_ctl reload -D "$PGDATA" || true  # || true ignores error if not running yet