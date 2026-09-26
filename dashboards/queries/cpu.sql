-- CPU cores busy: the usage counter's growth per second between two samples.
SELECT time, "CPU cores busy" FROM (
  SELECT
    sampled_at AS time,
    GREATEST(cpu_usage_usec - lag(cpu_usage_usec) OVER (ORDER BY sampled_at), 0)
      / NULLIF(extract(epoch FROM sampled_at - lag(sampled_at) OVER (ORDER BY sampled_at)) * 1e6, 0)
      AS "CPU cores busy"
  FROM ${SERVER_STATS_SCHEMA}.host_sample
  -- One sample before the range, so lag() gives the first point shown a real predecessor
  -- instead of a NULL rate; the outer filter then clips to the range.
  WHERE sampled_at >= :since - interval '1 hour'
) rates
WHERE time >= :since
ORDER BY time
