-- The tables read most over the time range, and how: sequential scans against index scans.
SELECT
  schemaname || '.' || relname AS "Table",
  max(seq_scan) - min(seq_scan) AS "Seq scans",
  max(idx_scan) - min(idx_scan) AS "Index scans",
  (max(seq_scan) - min(seq_scan)) + (max(idx_scan) - min(idx_scan)) AS "Total scans",
  round(((max(seq_scan) - min(seq_scan)) + (max(idx_scan) - min(idx_scan)))
        / GREATEST(extract(epoch FROM max(sampled_at) - min(sampled_at)) / 86400.0, 1), 3) AS "Scans/day",
  max(n_live_tup) AS "Live rows",
  max(heap_blks_read) - min(heap_blks_read) AS "Heap blocks read",
  max(idx_blks_read) - min(idx_blks_read) AS "Index blocks read"
FROM ${SERVER_STATS_SCHEMA}.table_sample
WHERE sampled_at >= :from AND sampled_at < :to
GROUP BY schemaname, relname
ORDER BY "Total scans" DESC NULLS LAST
LIMIT 20
