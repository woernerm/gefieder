-- Memory the system's containers use.
SELECT
  sampled_at        AS time,
  mem_current_bytes AS "Memory used"
FROM ${SERVER_STATS_SCHEMA}.host_sample
WHERE sampled_at >= :since
ORDER BY sampled_at
