-- Issues opened per day and project. In long form -- a row per day and project, the column
-- named "series" saying which -- which a chart turns into one series per project.
SELECT
  created_on AS day,
  tenant_id  AS series,
  count(*)   AS issues
FROM silver.issues
WHERE tenant_id = ANY(:project)
  AND state = ANY(:state)
GROUP BY created_on, tenant_id
ORDER BY created_on
