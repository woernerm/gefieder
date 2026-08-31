#!/bin/sh
# Server-statistics collector.
#
# Runs on the host as the rootless-podman user, a systemd user timer owning the cadence.
# Only the host can see the pod's real CPU time, disk IOPS and throughput, and network
# egress, so they are read from cgroup v2 and /sys and written into the database, where the
# per-query and per-table statistics already live. One INSERT per tick, then a rollup and
# prune so the raw table stays small.
#
# Everything is counters or sizes; rates are deltas between rows. Each metric is
# best-effort: a value that cannot be read is written NULL rather than failing the sample.
set -eu

# --- configuration --------------------------------------------------------------------
# The schema is fixed at build time, matching what the init scripts created. One sample per
# invocation; the timer decides how often.
APP_NAME="${APP_NAME:?APP_NAME must be set (server-stats.service and dev.sh provide it)}"
SCHEMA="${SERVER_STATS_SCHEMA:-server_stats}"

# Every pod container shares one parent cgroup, and postgresql is always present, so its
# cgroup's parent is the pod's.
CONTAINER="${SERVER_STATS_CONTAINER:-postgresql}"

# The size probes are far heavier than reading a counter file, and disk space does not move
# in seconds. Between probes the size columns are NULL and carry forward in the charts.
DISK_PROBE_SECONDS="${SERVER_STATS_DISK_PROBE_SECONDS:-300}"

# Both views hold cumulative counters, so a coarse cadence loses nothing while a
# per-minute snapshot of thousands of statements is what filled the raw tables. The host
# counters keep the full timer cadence.
QUERY_PROBE_SECONDS="${SERVER_STATS_QUERY_PROBE_SECONDS:-900}"

# --- helpers --------------------------------------------------------------------------
# field_in <file> <key> -> the value after "<key> " in a flat cgroup file, or empty.
field_in() {
    [ -r "$1" ] || return 0
    awk -v k="$2" '$1==k {print $2; found=1} END {if(!found) print ""}' "$1"
}

# Total <key> across io.stat's key=value lines, one per block device, so a volume spread
# over several is summed. Empty when the file is unreadable.
io_sum() {
    [ -r "$1" ] || { echo ""; return 0; }
    awk -v k="$2" '{
        for (i=2;i<=NF;i++){ n=index($i,"="); if(substr($i,1,n-1)==k) s+=substr($i,n+1) }
    } END { print s+0 }' "$1"
}

# An empty value has to reach SQL as a real NULL, not an empty string.
sql() { if [ -z "$1" ]; then printf NULL; else printf '%s' "$1"; fi; }

# True when <name> last ran at least <seconds> ago, marking it as run now. A marker file's
# mtime is the whole state, so a missed tick or a reboot makes the next one due.
due() {
    marker="$STATE_DIR/$1"
    last=0
    [ -f "$marker" ] && last="$(stat -c %Y "$marker" 2>/dev/null || echo 0)"
    [ $(( $(date +%s) - last )) -ge "$2" ] || return 1
    touch "$marker"
}

# psql inside the postgresql container, as the superuser. Required rather than defaulted:
# the superuser is created under SUPERUSER_NAME, so a hardcoded fallback would fit only a
# deployment that kept it.
POSTGRES_USER="${POSTGRES_USER:?POSTGRES_USER must be set to the database superuser name}"
psql_exec() {
    podman exec -i "$CONTAINER" \
        psql -v ON_ERROR_STOP=1 -qtAX \
        --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-postgres}" "$@"
}

# --- locate the pod cgroup ------------------------------------------------------------
# podman reports the container's own cgroup path; its parent aggregates the whole pod.
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
# io.stat appears only when the io controller is delegated to the user slice, which systemd
# does not do by default -- install.sh adds a drop-in. Without it, or on a kernel that
# hides per-cgroup io, these read empty and the columns are NULL.
IO_READ_BYTES="$(io_sum "${CG_POD}/io.stat" rbytes)"
IO_WRITE_BYTES="$(io_sum "${CG_POD}/io.stat" wbytes)"
IO_READ_IOS="$(io_sum "${CG_POD}/io.stat" rios)"
IO_WRITE_IOS="$(io_sum "${CG_POD}/io.stat" wios)"

# --- network egress/ingress (host interface) -----------------------------------------
# Pod traffic flows over the user-mode network's tap interface, whose name varies, so the
# counters of every non-loopback interface are summed. The monthly sum of tx deltas is the
# outgoing traffic.
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
# Only every DISK_PROBE_SECONDS; between probes these columns stay NULL.
DB_SIZE=""
TEMP_SIZE=""
VOLUME_SIZE=""
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/${APP_NAME}"
mkdir -p "$STATE_DIR"
if due "serverstats-disk-probe" "$DISK_PROBE_SECONDS"; then
    # From within the server, where pg_database_size and a directory-size sum are cheap.
    DB_SIZE="$(psql_exec -c "SELECT pg_database_size(current_database())" 2>/dev/null || echo "")"
    TEMP_SIZE="$(psql_exec -c "SELECT coalesce(sum((pg_ls_dir).size),0) FROM pg_ls_tmpdir() AS pg_ls_dir" 2>/dev/null || echo "")"
    # Total on-disk size of every data volume of the stack, summed over their mountpoints.
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

# --- snapshot the query and table statistics (slower sub-cadence, server-side) --------
# The views exist only inside the database, so this is one psql call entirely in SQL; the
# collector only triggers it at the cadence.
if due "serverstats-query-probe" "$QUERY_PROBE_SECONDS"; then
psql_exec <<SQL
-- One transaction, so the staged snapshot below lives across the three statements that
-- read it (ON COMMIT DROP) and all three see the same instant.
BEGIN;

-- pg_stat_statements reports the same queryid once per (userid, dbid, toplevel), so
-- aggregate by queryid to a single row per normalised statement: the total cost of a
-- query regardless of who ran it, which is what query optimisation cares about. This also
-- keeps queryid unique within the snapshot so it fits the (sampled_at, queryid) key.
CREATE TEMP TABLE _pgss ON COMMIT DROP AS
SELECT queryid, min(left(query, 2000)) AS query, sum(calls) AS calls,
       sum(total_exec_time) AS total_exec_time, sum(rows) AS rows,
       sum(shared_blks_hit) AS shared_blks_hit, sum(shared_blks_read) AS shared_blks_read,
       sum(temp_blks_read) AS temp_blks_read, sum(temp_blks_written) AS temp_blks_written
FROM pg_stat_statements
WHERE queryid IS NOT NULL
GROUP BY queryid;

-- The statement text goes in once per queryid and is then never written again.
INSERT INTO "$SCHEMA".query_dim (queryid, query)
SELECT queryid, query FROM _pgss
ON CONFLICT (queryid) DO NOTHING;

-- Store only the statements whose counters moved since their own last stored sample: an
-- unchanged row would contribute nothing to any delta the dashboard computes, and the
-- statements that ran in the last few minutes are a small fraction of the thousands
-- pg_stat_statements holds. A queryid never sampled before (no prev row) is always
-- stored, so every statement gets the baseline its later deltas are measured from.
-- calls is the discriminator: it is monotonic per queryid and rises whenever any of the
-- other counters can have. A pg_stat_statements reset makes it fall, and the row is then
-- stored too, so the reset is visible as a drop rather than silently flattening a delta.
INSERT INTO "$SCHEMA".query_sample (
    sampled_at, queryid, calls, total_exec_time, rows,
    shared_blks_hit, shared_blks_read, temp_blks_read, temp_blks_written
)
SELECT now(), c.queryid, c.calls, c.total_exec_time, c.rows,
       c.shared_blks_hit, c.shared_blks_read, c.temp_blks_read, c.temp_blks_written
FROM _pgss c
LEFT JOIN LATERAL (
    SELECT calls FROM "$SCHEMA".query_sample p
    WHERE p.queryid = c.queryid ORDER BY p.sampled_at DESC LIMIT 1
) prev ON true
WHERE prev.calls IS DISTINCT FROM c.calls;

-- Same for the per-table counters, with seq_scan + idx_scan as the discriminator: a table
-- nobody read since the last snapshot cannot have changed any of the columns that matter.
INSERT INTO "$SCHEMA".table_sample (
    sampled_at, schemaname, relname, seq_scan, seq_tup_read, idx_scan, idx_tup_fetch,
    n_live_tup, n_dead_tup, heap_blks_read, idx_blks_read
)
SELECT now(), t.schemaname, t.relname,
       t.seq_scan, t.seq_tup_read, coalesce(t.idx_scan,0), coalesce(t.idx_tup_fetch,0),
       t.n_live_tup, t.n_dead_tup,
       coalesce(io.heap_blks_read,0), coalesce(io.idx_blks_read,0)
FROM pg_stat_user_tables t
LEFT JOIN pg_statio_user_tables io ON io.relid = t.relid
LEFT JOIN LATERAL (
    SELECT p.seq_scan, p.idx_scan FROM "$SCHEMA".table_sample p
    WHERE p.schemaname = t.schemaname AND p.relname = t.relname
    ORDER BY p.sampled_at DESC LIMIT 1
) prev ON true
WHERE (prev.seq_scan, prev.idx_scan)
      IS DISTINCT FROM (t.seq_scan, coalesce(t.idx_scan, 0));

COMMIT;
SQL
fi

# --- drain the proxy's dashboard/page visit log ---------------------------------------
# The proxy writes one JSON line per page navigation to visits.log, having already
# discarded API, asset and non-GET noise. Only the lines added since the last tick are read,
# a byte offset in the state dir being the cursor; a file that shrank was rotated, so it is
# re-read from the start. Parsing and hashing happen server-side, so this just moves bytes.
VISIT_LOG="${SERVER_STATS_VISIT_LOG:-/var/log/app/visits.log}"
VISIT_CONTAINER="${SERVER_STATS_PROXY_CONTAINER:-proxy}"
OFFSET_FILE="$STATE_DIR/serverstats-visit-offset"

# Empty if the proxy or the file is absent.
cur_size="$(podman exec "$VISIT_CONTAINER" sh -c "wc -c < '$VISIT_LOG' 2>/dev/null" 2>/dev/null | tr -d ' ' || true)"
if [ -n "$cur_size" ]; then
    prev_size=0
    [ -f "$OFFSET_FILE" ] && prev_size="$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)"
    # A shrunk file was rotated or truncated.
    [ "$cur_size" -lt "$prev_size" ] && prev_size=0

    if [ "$cur_size" -gt "$prev_size" ]; then
        # The cursor must land on a newline: the proxy may still be writing the final
        # line, and a cursor left mid-line would feed a partial JSON fragment on every
        # future tick, wedging the drain for good. So only the complete-line prefix is
        # consumed and a trailing partial line is left for the next tick.
        NEW_BYTES="$(mktemp)"
        podman exec "$VISIT_CONTAINER" sh -c "tail -c +$((prev_size + 1)) '$VISIT_LOG'" > "$NEW_BYTES" 2>/dev/null || true
        # In bytes (LC_ALL=C), so a multibyte user-agent cannot skew the offset.
        total_new="$(LC_ALL=C wc -c < "$NEW_BYTES" | tr -d ' ')"
        if [ "$(tail -c1 "$NEW_BYTES" | od -An -tx1 | tr -d ' \n')" = "0a" ]; then
            # Every byte is part of a complete line.
            consumed="$total_new"
        else
            # Subtract the trailing partial line, everything after the last newline. With
            # no newline at all that is the whole buffer, so consumed stays 0.
            partial="$(sed '$!d' "$NEW_BYTES" | LC_ALL=C wc -c | tr -d ' ')"
            consumed=$(( total_new - partial ))
        fi

        if [ "${consumed:-0}" -gt 0 ]; then
            # One psql script: stage the lines with an inline COPY, whose data follows in
            # the same stream, then transform them. md5() makes the session cookie a
            # stable, non-reversible hash, so a person is never identifiable.
            VISIT_SQL="$(mktemp)"
            {
                printf 'CREATE TEMP TABLE _visit_raw (line text);\n'
                # A delimiter and quote that never occur in a JSON log line make COPY take
                # each line verbatim. The default TEXT format processes backslash escapes,
                # so a line containing one would be mangled or raise a COPY error.
                printf "\\\\copy _visit_raw FROM STDIN WITH (FORMAT csv, DELIMITER E'\\\\x1f', QUOTE E'\\\\x01')\\n"
                head -c "$consumed" "$NEW_BYTES"
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
-- Keep only lines that parse as JSON, so a single malformed line (e.g. one the proxy
-- half-wrote across a rotation) is skipped rather than aborting the whole INSERT.
-- pg_input_is_valid tests the cast without raising, unlike a bare line::jsonb.
FROM (
    SELECT line::jsonb AS j FROM _visit_raw
    WHERE line <> '' AND pg_input_is_valid(line, 'jsonb')
) s;
SQL
            } > "$VISIT_SQL"
            # Over stdin, which podman exec -i forwards into the container and \copy reads
            # the data from; -f with a host path would fail, psql running inside. The
            # cursor advances by the bytes consumed, and only on success, so a transient
            # failure re-reads them rather than dropping visits.
            if psql_exec < "$VISIT_SQL" >/dev/null 2>&1; then
                printf '%s' "$((prev_size + consumed))" > "$OFFSET_FILE"
            fi
            rm -f "$VISIT_SQL"
        fi
        rm -f "$NEW_BYTES"
    fi
fi

# --- roll up and prune ----------------------------------------------------------------
psql_exec -c "SELECT \"$SCHEMA\".rollup_and_prune();"
