#!/bin/sh
# Gefieder server-statistics collector.
#
# Runs on the host as the rootless-podman user (a systemd user timer fires it every
# SERVER_STATS_INTERVAL seconds), NOT inside a container: only the host can see the pod's
# real CPU time, disk IOPS and throughput, and network egress. PostgreSQL itself cannot
# report those, so they are read here from cgroup v2 and /sys and written into the
# database, where the per-query and per-table statistics already live. One INSERT per tick
# into the server-statistics schema, then a rollup/prune so the raw table stays small.
#
# Everything is plain counters or sizes; rates are computed later as deltas between rows.
# Each metric is best-effort: a value that cannot be read this tick is written as NULL
# rather than failing the whole sample, so a kernel that hides one cgroup file still
# yields a usable row.
set -eu

# --- configuration --------------------------------------------------------------------
# The schema name and the sampling interval come from the same config files the rest of
# the system uses. The schema is fixed at build time (it must match what the init scripts
# created); the interval is read from runtime.env so it can change without a rebuild. The
# collector itself runs once per invocation; the timer owns the cadence, but the disk
# sub-cadence below uses the interval to decide how often to run the expensive size probes.
APP_NAME="${APP_NAME:-gefieder}"
SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}"
RUNTIME_ENV="${RUNTIME_ENV:-$HOME/.config/${APP_NAME}/runtime.env}"
[ -f "$RUNTIME_ENV" ] && . "$RUNTIME_ENV"
INTERVAL="${SERVER_STATS_INTERVAL:-60}"

# The container whose cgroup stands in for the whole pod: all pod containers share one
# parent cgroup, and postgresql is always present, so its cgroup's parent is the pod's.
CONTAINER="${SERVER_STATS_CONTAINER:-postgresql}"

# Run the size probes (du/df/pg_database_size) only every ~5 minutes: disk space does not
# move in seconds and these probes are far heavier than reading a counter file. Between
# probes the size columns are written NULL and simply carry forward in the charts.
DISK_PROBE_SECONDS="${SERVER_STATS_DISK_PROBE_SECONDS:-300}"

# --- helpers --------------------------------------------------------------------------
# Read a single whitespace-delimited field from a flat cgroup file like cpu.stat:
#   field_in <file> <key>   ->  the value after "<key> " or empty if absent/unreadable.
field_in() {
    [ -r "$1" ] || return 0
    awk -v k="$2" '$1==k {print $2; found=1} END {if(!found) print ""}' "$1"
}

# Sum a column across the io.stat lines (one line per block device). io.stat fields are
# key=value pairs; pick the value of <key> on every line and total them, so a volume
# spread over several devices is summed. Empty when the file is unreadable.
io_sum() {
    [ -r "$1" ] || { echo ""; return 0; }
    awk -v k="$2" '{
        for (i=2;i<=NF;i++){ n=index($i,"="); if(substr($i,1,n-1)==k) s+=substr($i,n+1) }
    } END { print s+0 }' "$1"
}

# Emit NULL for an empty value so it lands in SQL as a real NULL, not an empty string.
sql() { if [ -z "$1" ]; then printf NULL; else printf '%s' "$1"; fi; }

# Run psql inside the postgresql container as the superuser against the app database.
psql_exec() {
    podman exec -i "$CONTAINER" \
        psql -v ON_ERROR_STOP=1 -qtAX \
        --username "${POSTGRES_USER:-admin}" --dbname "${POSTGRES_DB:-postgres}" "$@"
}

# --- locate the pod cgroup ------------------------------------------------------------
# podman reports the container's own cgroup path; its parent directory is the pod cgroup
# that aggregates every container in the pod, which is what we want to measure.
CG_CONTAINER="$(podman inspect "$CONTAINER" --format '{{.State.CgroupPath}}' 2>/dev/null || true)"
CG_ROOT="/sys/fs/cgroup"
if [ -n "$CG_CONTAINER" ] && [ -d "${CG_ROOT}${CG_CONTAINER}" ]; then
    CG_POD="${CG_ROOT}$(dirname "$CG_CONTAINER")"
else
    CG_POD=""   # cgroup unavailable (e.g. cgroup v1); host counters fall back to NULL
fi

# --- CPU + memory (cgroup) ------------------------------------------------------------
CPU_USAGE_USEC="$(field_in "${CG_POD}/cpu.stat" usage_usec)"
MEM_CURRENT="$( [ -r "${CG_POD}/memory.current" ] && cat "${CG_POD}/memory.current" || echo "" )"
MEM_PEAK="$(    [ -r "${CG_POD}/memory.peak" ]    && cat "${CG_POD}/memory.peak"    || echo "" )"
HOST_NPROC="$(nproc 2>/dev/null || echo "")"
HOST_MEM_TOTAL="$(field_in /proc/meminfo MemTotal:)"   # value is in kB
[ -n "$HOST_MEM_TOTAL" ] && HOST_MEM_TOTAL=$((HOST_MEM_TOTAL * 1024))

# --- disk I/O (cgroup io.stat) --------------------------------------------------------
# io.stat only appears here when the io controller is delegated to the user slice. systemd
# does not delegate it by default, so install.sh adds a drop-in that does; without it (or
# on a kernel that hides per-cgroup io, e.g. some WSL2 builds) these read empty and the
# columns are written NULL rather than failing the sample.
IO_READ_BYTES="$(io_sum "${CG_POD}/io.stat" rbytes)"
IO_WRITE_BYTES="$(io_sum "${CG_POD}/io.stat" wbytes)"
IO_READ_IOS="$(io_sum "${CG_POD}/io.stat" rios)"
IO_WRITE_IOS="$(io_sum "${CG_POD}/io.stat" wios)"

# --- network egress/ingress (host interface) -----------------------------------------
# Rootless podman's pod traffic flows over the user-mode network's tap interface. Sum the
# tx/rx byte counters of every non-loopback interface, so the value holds regardless of
# the interface name (pasta's tap, slirp4netns's tap0, ...). The monthly sum of tx deltas
# is the outgoing traffic.
NET_TX=""
NET_RX=""
for dir in /sys/class/net/*; do
    ifn="$(basename "$dir")"
    [ "$ifn" = "lo" ] && continue
    tx="$(cat "$dir/statistics/tx_bytes" 2>/dev/null || echo 0)"
    rx="$(cat "$dir/statistics/rx_bytes" 2>/dev/null || echo 0)"
    NET_TX=$(( ${NET_TX:-0} + tx ))
    NET_RX=$(( ${NET_RX:-0} + rx ))
done

# --- sizes (slower sub-cadence) -------------------------------------------------------
# Probe the database, temp-spill and volume sizes only every DISK_PROBE_SECONDS. A small
# marker file's mtime tracks the last probe; between probes these columns stay NULL.
DB_SIZE=""
TEMP_SIZE=""
VOLUME_SIZE=""
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/${APP_NAME}"
mkdir -p "$STATE_DIR"
MARKER="$STATE_DIR/serverstats-disk-probe"
now_epoch="$(date +%s)"
last_probe=0
[ -f "$MARKER" ] && last_probe="$(stat -c %Y "$MARKER" 2>/dev/null || echo 0)"
if [ $(( now_epoch - last_probe )) -ge "$DISK_PROBE_SECONDS" ]; then
    touch "$MARKER"
    # Database size and the temp spill (DuckDB/sort spill in base/pgsql_tmp) from within
    # the server; pg_database_size and a directory-size sum are both cheap server-side.
    DB_SIZE="$(psql_exec -c "SELECT pg_database_size(current_database())" 2>/dev/null || echo "")"
    TEMP_SIZE="$(psql_exec -c "SELECT coalesce(sum((pg_ls_dir).size),0) FROM pg_ls_tmpdir() AS pg_ls_dir" 2>/dev/null || echo "")"
    # Total on-disk size of every Gefieder data volume, summed over their mountpoints.
    vs=0
    for vol in $(podman volume ls --format '{{.Name}}' 2>/dev/null | grep -E '_data$' || true); do
        mp="$(podman volume inspect "$vol" --format '{{.Mountpoint}}' 2>/dev/null || true)"
        [ -n "$mp" ] && [ -d "$mp" ] || continue
        bytes="$(du -sb "$mp" 2>/dev/null | awk '{print $1}')"
        vs=$(( vs + ${bytes:-0} ))
    done
    VOLUME_SIZE="$vs"
fi

# --- write the host sample ------------------------------------------------------------
psql_exec -v schema="$SCHEMA" <<SQL
INSERT INTO "$SCHEMA".host_sample (
    cpu_usage_usec, host_nproc,
    mem_current_bytes, mem_peak_bytes, host_mem_total_bytes,
    io_read_bytes, io_write_bytes, io_read_ios, io_write_ios,
    net_tx_bytes, net_rx_bytes,
    db_size_bytes, temp_size_bytes, volume_size_bytes
) VALUES (
    $(sql "$CPU_USAGE_USEC"), $(sql "$HOST_NPROC"),
    $(sql "$MEM_CURRENT"), $(sql "$MEM_PEAK"), $(sql "$HOST_MEM_TOTAL"),
    $(sql "$IO_READ_BYTES"), $(sql "$IO_WRITE_BYTES"), $(sql "$IO_READ_IOS"), $(sql "$IO_WRITE_IOS"),
    $(sql "$NET_TX"), $(sql "$NET_RX"),
    $(sql "$DB_SIZE"), $(sql "$TEMP_SIZE"), $(sql "$VOLUME_SIZE")
);
SQL

# --- snapshot the query and table statistics (server-side) ----------------------------
# These read views that only exist inside the database, so they are done in one psql call
# entirely in SQL. The collector's job is just to trigger the snapshot at the cadence.
psql_exec <<SQL
INSERT INTO "$SCHEMA".query_sample (
    sampled_at, queryid, query, calls, total_exec_time, rows,
    shared_blks_hit, shared_blks_read, temp_blks_read, temp_blks_written
)
-- pg_stat_statements reports the same queryid once per (userid, dbid, toplevel), so
-- aggregate by queryid to a single row per normalised statement: the total cost of a
-- query regardless of who ran it, which is what query optimisation cares about. This also
-- keeps queryid unique within the snapshot so it fits the (sampled_at, queryid) key.
SELECT now(), queryid, min(left(query, 2000)), sum(calls), sum(total_exec_time), sum(rows),
       sum(shared_blks_hit), sum(shared_blks_read), sum(temp_blks_read), sum(temp_blks_written)
FROM pg_stat_statements
WHERE queryid IS NOT NULL
GROUP BY queryid;

INSERT INTO "$SCHEMA".table_sample (
    sampled_at, schemaname, relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch,
    n_live_tup, n_dead_tup, heap_blks_read, idx_blks_read
)
SELECT now(), t.schemaname, t.relname,
       t.seq_scan, t.seq_tup_read, coalesce(t.idx_scan,0), coalesce(t.idx_tup_fetch,0),
       t.n_live_tup, t.n_dead_tup,
       coalesce(io.heap_blks_read,0), coalesce(io.idx_blks_read,0)
FROM pg_stat_user_tables t
LEFT JOIN pg_statio_user_tables io ON io.relid = t.relid;
SQL

# --- drain the proxy's dashboard/page visit log ---------------------------------------
# The proxy writes one JSON line per page navigation to visits.log (it already discards
# API/asset/non-GET noise). Read only the lines added since last tick -- a byte offset
# kept in the state dir is the cursor -- and load them. If the file shrank since last time
# (rotated/truncated), restart from the beginning. Parsing, the cookie hashing and the
# dashboard-uid extraction are all done server-side in SQL below, so this just moves bytes.
VISIT_LOG="${SERVER_STATS_VISIT_LOG:-/var/log/gefieder/visits.log}"
VISIT_CONTAINER="${SERVER_STATS_PROXY_CONTAINER:-proxy}"
OFFSET_FILE="$STATE_DIR/serverstats-visit-offset"

# Current size of the log inside the proxy container; empty if the proxy or file is absent.
cur_size="$(podman exec "$VISIT_CONTAINER" sh -c "wc -c < '$VISIT_LOG' 2>/dev/null" 2>/dev/null | tr -d ' ' || true)"
if [ -n "$cur_size" ]; then
    prev_size=0
    [ -f "$OFFSET_FILE" ] && prev_size="$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)"
    # A shrunk file means it was rotated/truncated; re-read from the start.
    [ "$cur_size" -lt "$prev_size" ] && prev_size=0

    if [ "$cur_size" -gt "$prev_size" ]; then
        # Build a single psql script: stage the new lines with an inline COPY (the data
        # follows the \copy command in the same stream, terminated by \.), then transform
        # them. md5() turns the raw session cookie into a stable, non-reversible hash so a
        # person is never identifiable; the dashboard uid is parsed from /d/<uid>/<slug>.
        # A half-written final line cannot abort the drain: jsonb parse errors are avoided
        # by skipping blank lines, and any bad line is re-read whole on the next tick.
        VISIT_SQL="$(mktemp)"
        {
            printf 'CREATE TEMP TABLE _visit_raw (line text);\n'
            printf '\\copy _visit_raw FROM STDIN\n'
            podman exec "$VISIT_CONTAINER" sh -c "tail -c +$((prev_size + 1)) '$VISIT_LOG'" 2>/dev/null
            printf '\\.\n'
            cat <<SQL
INSERT INTO "$SCHEMA".dashboard_visit
    (visited_at, app, url_path, dashboard_uid, client_ip, session_hash, status, user_agent)
SELECT
    (j->>'ts')::timestamptz,
    j->>'app',
    j->>'path',
    -- The Grafana dashboard uid is the path segment after /d/ or /d-solo/; NULL otherwise.
    substring(j->>'path' FROM '/d(?:-solo)?/([^/]+)'),
    -- Prefer the original client behind a forwarding proxy, else the direct peer.
    coalesce(nullif(split_part(j->>'xff', ',', 1), ''), j->>'ip'),
    -- Hash whichever session cookie is present; never store the cookie itself.
    md5(coalesce(nullif(j->>'grafana_session',''), nullif(j->>'crudman_session',''), '')),
    nullif(j->>'status','')::int,
    j->>'ua'
FROM (SELECT line::jsonb AS j FROM _visit_raw WHERE line <> '') s;
SQL
        } > "$VISIT_SQL"
        # Feed the script (commands + inline COPY data) to psql over stdin, which podman
        # exec -i forwards into the container; \copy then reads the data from that same
        # stream. Passing -f a host path would fail, since psql runs inside the container.
        # Only advance the cursor if the load succeeded, so a transient failure re-reads
        # the same bytes next tick rather than dropping visits.
        if psql_exec < "$VISIT_SQL" >/dev/null 2>&1; then
            printf '%s' "$cur_size" > "$OFFSET_FILE"
        fi
        rm -f "$VISIT_SQL"
    fi
fi

# --- roll up and prune ----------------------------------------------------------------
psql_exec -c "SELECT \"$SCHEMA\".rollup_and_prune();"
