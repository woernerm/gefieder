# Create the server-statistics schema that holds the data used to size a future server
# (CPU, RAM, temp/fast storage, disk space, disk IOPS and throughput, network egress) and
# to find queries worth an index. The schema name comes from SERVER_STATS_SCHEMA, baked
# into the image from buildtime.env; it is interpolated here because psql cannot template
# an identifier inside a plain .sql file. All identifiers go through %I/quote_ident so the
# configured name cannot inject SQL.
#
# Three raw sample tables are filled by the collector, one snapshot per tick:
#   host_sample   -- one row of host/cgroup/network counters (the sizing inputs)
#   query_sample  -- a pg_stat_statements snapshot (which queries cost the most)
#   table_sample  -- a pg_stat_user_tables / pg_statio snapshot (which table needs an index)
# The host/IO/network values are monotonic counters, stored raw so a rate is a delta
# between two rows; this is restart-safe (a counter reset just shows up as a single
# ignored negative delta) and lets the display pick any window.
#
# rollup_and_prune() aggregates the raw rows into hourly buckets kept long-term and drops
# raw rows older than the retention window, so the raw tables stay small over months while
# the hourly history remains for the long sizing horizon. The collector calls it each tick.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v schema="$SERVER_STATS_SCHEMA" <<'SQL'
-- The schema is owned by the superuser (the init connection); the collector writes to it
-- as the superuser too, so no extra write role is needed. grafana gets read access below.
CREATE SCHEMA IF NOT EXISTS :"schema";

--------------------------------------------------------------------------------
-- Raw host/resource counters: one row per collector tick.
--------------------------------------------------------------------------------
-- Every *_bytes / *_usec / *_ios column is a monotonic counter read from the pod's
-- cgroup (cpu.stat, memory.current/peak, io.stat) or the host network interface. The
-- *_size_bytes columns are absolute sizes (disk space, temp spill), not counters. A rate
-- is the difference between two consecutive rows divided by their time difference.
CREATE TABLE IF NOT EXISTS :"schema".host_sample (
    sampled_at          timestamptz NOT NULL DEFAULT now() PRIMARY KEY,
    -- CPU: cumulative CPU time the pod has used, in microseconds (cgroup cpu.stat
    -- usage_usec). delta / interval gives the average number of vCPUs busy. nproc is the
    -- host core count, the ceiling to size against.
    cpu_usage_usec      bigint,
    host_nproc          int,
    -- Memory: current and peak resident bytes of the pod (cgroup memory.current/peak) and
    -- the host total, for sizing RAM against the observed peak.
    mem_current_bytes   bigint,
    mem_peak_bytes      bigint,
    host_mem_total_bytes bigint,
    -- Disk I/O of the data volume's backing device (cgroup io.stat), as cumulative bytes
    -- and operation counts; deltas give throughput (B/s) and IOPS, read and write split.
    io_read_bytes       bigint,
    io_write_bytes      bigint,
    io_read_ios         bigint,
    io_write_ios        bigint,
    -- Network egress/ingress of the pod interface (host /sys statistics), cumulative
    -- bytes; the tx delta summed over a month is the outgoing traffic in GB/month.
    net_tx_bytes        bigint,
    net_rx_bytes        bigint,
    -- Absolute sizes (not counters), refreshed on the slower disk sub-cadence: total
    -- on-disk size of the database and the temp/spill area that wants fast storage.
    db_size_bytes       bigint,
    temp_size_bytes     bigint,
    -- Total bytes of all data volumes on disk, for sizing total disk space.
    volume_size_bytes   bigint
);

--------------------------------------------------------------------------------
-- pg_stat_statements snapshot: which queries cost the most.
--------------------------------------------------------------------------------
-- A snapshot per tick of the cumulative per-query counters. Compared across two rows it
-- shows which statements grew their time / temp / shared-block reads, i.e. where an index
-- or a rewrite pays off. queryid identifies the normalised statement across snapshots.
CREATE TABLE IF NOT EXISTS :"schema".query_sample (
    sampled_at      timestamptz NOT NULL DEFAULT now(),
    queryid         bigint,
    query           text,
    calls           bigint,
    total_exec_time double precision,
    rows            bigint,
    shared_blks_hit  bigint,
    shared_blks_read bigint,
    temp_blks_read   bigint,
    temp_blks_written bigint,
    PRIMARY KEY (sampled_at, queryid)
);

--------------------------------------------------------------------------------
-- Per-table access snapshot: which table needs an index.
--------------------------------------------------------------------------------
-- A snapshot per tick of pg_stat_user_tables / pg_statio_user_tables. A table whose
-- seq_scan grows much faster than idx_scan while it holds many live rows is the textbook
-- candidate for a new index; the live-tuple and block-read columns size the win.
CREATE TABLE IF NOT EXISTS :"schema".table_sample (
    sampled_at       timestamptz NOT NULL,
    schemaname       text,
    relname          text,
    seq_scan         bigint,
    seq_tup_read     bigint,
    idx_scan         bigint,
    idx_tup_fetch    bigint,
    n_live_tup       bigint,
    n_dead_tup       bigint,
    heap_blks_read   bigint,
    idx_blks_read    bigint,
    PRIMARY KEY (sampled_at, schemaname, relname)
);

--------------------------------------------------------------------------------
-- Hourly rollup of the host counters, kept long-term for the sizing horizon.
--------------------------------------------------------------------------------
-- One row per hour: the min/max/avg of each absolute column and the per-hour counter
-- deltas (already differenced, so summing them over a month is the monthly total). bucket
-- is the truncated hour. This is the table the sizing dashboard reads over many months.
CREATE TABLE IF NOT EXISTS :"schema".host_hourly (
    bucket               timestamptz NOT NULL PRIMARY KEY,
    samples              int,
    -- Averages and peaks of the absolute gauges.
    cpu_cores_avg        double precision,
    cpu_cores_max        double precision,
    mem_bytes_avg        double precision,
    mem_bytes_max        bigint,
    db_size_bytes_max    bigint,
    temp_size_bytes_max  bigint,
    volume_size_bytes_max bigint,
    -- Per-hour counter deltas (sum over a period = total for that period).
    io_read_bytes_sum    bigint,
    io_write_bytes_sum   bigint,
    io_read_ios_sum      bigint,
    io_write_ios_sum     bigint,
    net_tx_bytes_sum     bigint,
    net_rx_bytes_sum     bigint
);

--------------------------------------------------------------------------------
-- Rollup + prune, called by the collector each tick.
--------------------------------------------------------------------------------
-- raw_retention keeps two weeks of 1-minute rows for fine-grained inspection; everything
-- older is dropped after it has been folded into host_hourly. hourly rows are kept far
-- longer (pruned to ~13 months) so a full year of seasonality is available for sizing.
CREATE OR REPLACE FUNCTION :"schema".rollup_and_prune(
    raw_retention   interval DEFAULT interval '14 days',
    hourly_retention interval DEFAULT interval '13 months'
)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, :"schema"
AS $$
BEGIN
    -- Recompute the rollup for every hour that still has raw rows, so the most recent
    -- (still-filling) bucket is refreshed each call and closed buckets are idempotent.
    -- The counter columns use a window LAG within the hour to turn the monotonic counters
    -- into per-sample deltas, ignoring negative deltas from a counter reset (GREATEST 0).
    INSERT INTO host_hourly AS h
    SELECT
        date_trunc('hour', sampled_at)                                   AS bucket,
        count(*)                                                         AS samples,
        avg(cpu_cores)                                                   AS cpu_cores_avg,
        max(cpu_cores)                                                   AS cpu_cores_max,
        avg(mem_current_bytes)                                          AS mem_bytes_avg,
        max(mem_current_bytes)                                          AS mem_bytes_max,
        max(db_size_bytes)                                             AS db_size_bytes_max,
        max(temp_size_bytes)                                          AS temp_size_bytes_max,
        max(volume_size_bytes)                                       AS volume_size_bytes_max,
        sum(d_io_read_bytes)                                            AS io_read_bytes_sum,
        sum(d_io_write_bytes)                                           AS io_write_bytes_sum,
        sum(d_io_read_ios)                                             AS io_read_ios_sum,
        sum(d_io_write_ios)                                            AS io_write_ios_sum,
        sum(d_net_tx_bytes)                                            AS net_tx_bytes_sum,
        sum(d_net_rx_bytes)                                            AS net_rx_bytes_sum
    FROM (
        SELECT
            sampled_at,
            mem_current_bytes, db_size_bytes, temp_size_bytes, volume_size_bytes,
            -- CPU cores busy since the previous sample: cpu-usec delta over the wall-clock
            -- microseconds between samples.
            GREATEST(cpu_usage_usec - lag(cpu_usage_usec) OVER w, 0)
                / NULLIF(extract(epoch FROM sampled_at - lag(sampled_at) OVER w) * 1e6, 0)
                                                                       AS cpu_cores,
            GREATEST(io_read_bytes  - lag(io_read_bytes)  OVER w, 0)  AS d_io_read_bytes,
            GREATEST(io_write_bytes - lag(io_write_bytes) OVER w, 0)  AS d_io_write_bytes,
            GREATEST(io_read_ios    - lag(io_read_ios)    OVER w, 0)  AS d_io_read_ios,
            GREATEST(io_write_ios   - lag(io_write_ios)   OVER w, 0)  AS d_io_write_ios,
            GREATEST(net_tx_bytes   - lag(net_tx_bytes)   OVER w, 0)  AS d_net_tx_bytes,
            GREATEST(net_rx_bytes   - lag(net_rx_bytes)   OVER w, 0)  AS d_net_rx_bytes
        FROM host_sample
        WINDOW w AS (ORDER BY sampled_at)
    ) deltas
    GROUP BY 1
    ON CONFLICT (bucket) DO UPDATE SET
        samples               = excluded.samples,
        cpu_cores_avg         = excluded.cpu_cores_avg,
        cpu_cores_max         = excluded.cpu_cores_max,
        mem_bytes_avg         = excluded.mem_bytes_avg,
        mem_bytes_max         = excluded.mem_bytes_max,
        db_size_bytes_max     = excluded.db_size_bytes_max,
        temp_size_bytes_max   = excluded.temp_size_bytes_max,
        volume_size_bytes_max = excluded.volume_size_bytes_max,
        io_read_bytes_sum     = excluded.io_read_bytes_sum,
        io_write_bytes_sum    = excluded.io_write_bytes_sum,
        io_read_ios_sum       = excluded.io_read_ios_sum,
        io_write_ios_sum      = excluded.io_write_ios_sum,
        net_tx_bytes_sum      = excluded.net_tx_bytes_sum,
        net_rx_bytes_sum      = excluded.net_rx_bytes_sum;

    -- Drop raw rows past the retention window (they are now captured in host_hourly), and
    -- the per-query / per-table snapshots, which are only useful for the recent window.
    DELETE FROM host_sample  WHERE sampled_at < now() - raw_retention;
    DELETE FROM query_sample WHERE sampled_at < now() - raw_retention;
    DELETE FROM table_sample WHERE sampled_at < now() - raw_retention;
    DELETE FROM host_hourly  WHERE bucket     < now() - hourly_retention;
END;
$$;

--------------------------------------------------------------------------------
-- Read access for grafana so the display layer (added later) can chart the data.
--------------------------------------------------------------------------------
GRANT USAGE ON SCHEMA :"schema" TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA :"schema" TO grafana;
ALTER DEFAULT PRIVILEGES IN SCHEMA :"schema" GRANT SELECT ON TABLES TO grafana;
SQL
