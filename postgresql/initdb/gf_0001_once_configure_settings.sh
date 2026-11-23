# pg_stat_statements records the per-query statistics the collector snapshots to find the
# queries worth optimising. It takes effect at the restart initdb performs before opening
# for connections, not at the reload below.
echo "shared_preload_libraries = 'pg_stat_statements'" >> "$PGDATA/postgresql.conf"
# The default 'top' misses statements nested in functions, so a SQLMesh model wrapped in a
# call would not show up.
echo "pg_stat_statements.track = all" >> "$PGDATA/postgresql.conf"

# To stderr, which podman forwards to journald; journald rotates and size-caps the log,
# where a file under $PGDATA/log grew unbounded next to the data.
#
# The default stderr format rather than jsonlog: journald stamps each entry on reception,
# so a multi-line statement detail leaves no untimestamped continuation lines, which is
# what jsonlog was chosen for.
#
# The prefix carries the backend pid but no "%m", journald stamping every entry already.
# It costs the server's millisecond emission time for journald's reception time.
echo "log_line_prefix = '[%p] '" >> "$PGDATA/postgresql.conf"

# Apply the new settings.
pg_ctl reload -D "$PGDATA" || true  # || true ignores error if not running yet