-- Bytes read and written per second, from the growth of the I/O counters between samples.
SELECT time, "Read", "Write" FROM (
  SELECT
    sampled_at AS time,
    GREATEST(io_read_bytes - lag(io_read_bytes) OVER (ORDER BY sampled_at), 0)
      / NULLIF(extract(epoch FROM sampled_at - lag(sampled_at) OVER (ORDER BY sampled_at)), 0) AS "Read",
    GREATEST(io_write_bytes - lag(io_write_bytes) OVER (ORDER BY sampled_at), 0)
      / NULLIF(extract(epoch FROM sampled_at - lag(sampled_at) OVER (ORDER BY sampled_at)), 0) AS "Write"
  FROM ${SERVER_STATS_SCHEMA}.host_sample
  -- One sample before the range; see cpu.sql.
  WHERE sampled_at >= :since - interval '1 hour'
) rates
WHERE time >= :since
ORDER BY time
