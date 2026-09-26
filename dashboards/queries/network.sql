-- Bytes sent and received per second, from the growth of the network counters.
SELECT time, "Sent", "Received" FROM (
  SELECT
    sampled_at AS time,
    GREATEST(net_tx_bytes - lag(net_tx_bytes) OVER (ORDER BY sampled_at), 0)
      / NULLIF(extract(epoch FROM sampled_at - lag(sampled_at) OVER (ORDER BY sampled_at)), 0) AS "Sent",
    GREATEST(net_rx_bytes - lag(net_rx_bytes) OVER (ORDER BY sampled_at), 0)
      / NULLIF(extract(epoch FROM sampled_at - lag(sampled_at) OVER (ORDER BY sampled_at)), 0) AS "Received"
  FROM ${SERVER_STATS_SCHEMA}.host_sample
  -- One sample before the range; see cpu.sql.
  WHERE sampled_at >= :since - interval '1 hour'
) rates
WHERE time >= :since
ORDER BY time
