-- How many issues are in each state.
SELECT
  state,
  count(*) AS "Issues"
FROM silver.issues
WHERE tenant_id = ANY(:project)
GROUP BY state
ORDER BY state
