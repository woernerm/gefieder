# Allow unsigned and community extensions for all future sessions. However, this only
# takes effect after restarting PostgreSQL or reloading the configuration.
echo "duckdb.allow_unsigned_extensions = true" >> "$PGDATA/postgresql.conf"
echo "duckdb.allow_community_extensions = true" >> "$PGDATA/postgresql.conf"

# Read the DuckDB extensions from the image instead of the default location inside
# $PGDATA. The image ships them pre-downloaded (see the Dockerfile), so the server never
# needs internet access to fetch one, and a fresh data volume starts out complete.
echo "duckdb.extension_directory = '/opt/duckdb-extensions'" >> "$PGDATA/postgresql.conf"

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

# Log to the container's stderr (the server default, so logging_collector stays off),
# which podman forwards to journald like every other service. journald rotates and
# size-caps the log; a file under $PGDATA/log did not, and grew unbounded next to the
# data. log_min_messages stays at the server default.
#
# The default stderr format is kept rather than jsonlog: journald records each entry with
# its own reception timestamp, so a multi-line statement detail no longer leaves
# untimestamped continuation lines on disk, which was the reason jsonlog was chosen.
echo "log_line_prefix = '%m [%p] '" >> "$PGDATA/postgresql.conf"

# Reload PostgreSQL configuration to apply the new settings.
pg_ctl reload -D "$PGDATA" || true  # || true ignores error if not running yet