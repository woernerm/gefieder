-- Every issue, newest first.
SELECT
  tenant_id  AS "Project",
  issue_id   AS "Issue",
  title      AS "Title",
  state      AS "State",
  created_on AS "Created",
  effort     AS "Effort"
FROM silver.issues
WHERE tenant_id = ANY(:project)
  AND state = ANY(:state)
ORDER BY created_on DESC, issue_id
