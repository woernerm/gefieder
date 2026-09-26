-- Traffic sent and received over the time range: the sum of the positive per-sample deltas
-- of the monotonic network counters. Reaches one sample before the range, so the first
-- delta inside it is not lost, then clips the sum to the range.
SELECT
  sum(sent)     AS "Sent",
  sum(received) AS "Received"
FROM (
  SELECT
    sampled_at,
    GREATEST(net_tx_bytes - lag(net_tx_bytes) OVER (ORDER BY sampled_at), 0) AS sent,
    GREATEST(net_rx_bytes - lag(net_rx_bytes) OVER (ORDER BY sampled_at), 0) AS received
  FROM ${SERVER_STATS_SCHEMA}.host_sample
  WHERE sampled_at >= :since - interval '1 hour'
) deltas
WHERE sampled_at >= :since
