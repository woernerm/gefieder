-- The statements run most over the time range, and what they cost.
SELECT
  left(query, 2000) AS "Query",
  max(calls) - min(calls) AS "Runs",
  round(((max(total_exec_time) - min(total_exec_time)) / 1000)::numeric, 3) AS "Time sec",
  round(((max(calls) - min(calls))
         / GREATEST(extract(epoch FROM max(sampled_at) - min(sampled_at)) / 86400.0, 1))::numeric, 3) AS "Runs/day",
  round(((max(total_exec_time) - min(total_exec_time)) / 1000
         / GREATEST(extract(epoch FROM max(sampled_at) - min(sampled_at)) / 86400.0, 1))::numeric, 3) AS "Time sec/day",
  CASE WHEN max(calls) - min(calls) > 0
    THEN round(((max(total_exec_time) - min(total_exec_time)) / 1000 / (max(calls) - min(calls)))::numeric, 3)
  END AS "Avg sec"
FROM ${SERVER_STATS_SCHEMA}.query_sample
  JOIN ${SERVER_STATS_SCHEMA}.query_dim USING (queryid)
WHERE sampled_at >= :from AND sampled_at < :to
GROUP BY queryid, query
ORDER BY "Runs" DESC NULLS LAST
LIMIT 20
