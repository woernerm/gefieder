-- The database, all volumes and the temporary spill space, in bytes.
SELECT time, "Database", "All volumes", "Temp/spill" FROM (
  SELECT
    time,
    -- The sizes are probed every few minutes and NULL in between, so a short range can hold
    -- only NULLs. Each carries its last value forward: grouping by the running count of
    -- non-NULL samples puts a run of NULLs in the group of the value before it.
    max(db_size_bytes)     OVER (PARTITION BY db_group)     AS "Database",
    max(volume_size_bytes) OVER (PARTITION BY volume_group) AS "All volumes",
    max(temp_size_bytes)   OVER (PARTITION BY temp_group)   AS "Temp/spill"
  FROM (
    SELECT
      sampled_at AS time, db_size_bytes, volume_size_bytes, temp_size_bytes,
      count(db_size_bytes)     OVER (ORDER BY sampled_at) AS db_group,
      count(volume_size_bytes) OVER (ORDER BY sampled_at) AS volume_group,
      count(temp_size_bytes)   OVER (ORDER BY sampled_at) AS temp_group
    FROM ${SERVER_STATS_SCHEMA}.host_sample
    -- Far enough back to include the size probe before the range starts.
    WHERE sampled_at >= :from - interval '1 hour' AND sampled_at < :to
  ) filled
) sizes
WHERE time >= :from
ORDER BY time
