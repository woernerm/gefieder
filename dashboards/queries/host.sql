-- What the server has: its CPU cores and memory, as last sampled.
SELECT
  (SELECT host_nproc FROM ${SERVER_STATS_SCHEMA}.host_sample
   WHERE host_nproc IS NOT NULL ORDER BY sampled_at DESC LIMIT 1) AS "Cores",
  (SELECT host_mem_total_bytes FROM ${SERVER_STATS_SCHEMA}.host_sample
   WHERE host_mem_total_bytes IS NOT NULL ORDER BY sampled_at DESC LIMIT 1) AS "Memory"
