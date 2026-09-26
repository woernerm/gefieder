-- What the system uses, sample by sample: CPU cores busy, memory, and bytes per second read,
-- written, sent and received. The counters are turned into rates by their growth between
-- two samples; one query, which each chart narrows to the columns it shows.
SELECT * FROM (
  SELECT
    sampled_at AS time,
    GREATEST(cpu_usage_usec - lag(cpu_usage_usec) OVER w, 0) / NULLIF(seconds * 1e6, 0) AS "CPU cores busy",
    mem_current_bytes AS "Memory used",
    GREATEST(io_read_bytes - lag(io_read_bytes) OVER w, 0) / NULLIF(seconds, 0) AS "Read",
    GREATEST(io_write_bytes - lag(io_write_bytes) OVER w, 0) / NULLIF(seconds, 0) AS "Write",
    GREATEST(net_tx_bytes - lag(net_tx_bytes) OVER w, 0) / NULLIF(seconds, 0) AS "Sent",
    GREATEST(net_rx_bytes - lag(net_rx_bytes) OVER w, 0) / NULLIF(seconds, 0) AS "Received"
  FROM (
    SELECT *, extract(epoch FROM sampled_at - lag(sampled_at) OVER (ORDER BY sampled_at)) AS seconds
    FROM ${SERVER_STATS_SCHEMA}.host_sample
    -- One sample before the range, so lag() gives the first point shown a real
    -- predecessor instead of a NULL rate; the outer filter then clips to the range.
    WHERE sampled_at >= :since - interval '1 hour'
  ) samples
  WINDOW w AS (ORDER BY sampled_at)
) rates
WHERE time >= :since
ORDER BY time
