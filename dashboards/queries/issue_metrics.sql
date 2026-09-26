-- Open and closed issues per project, as gold precomputes them.
SELECT
  tenant_id     AS project,
  open_issues   AS "Open",
  closed_issues AS "Closed"
FROM gold.issue_metrics
WHERE tenant_id = ANY(:project)
ORDER BY project
