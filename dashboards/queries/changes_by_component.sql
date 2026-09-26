-- How often the state, effort or safety classification of an issue changed, per component:
-- where the churn is.
SELECT
  component_id AS component,
  count(*)     AS "Changes"
FROM silver.issue_risk_history
WHERE tenant_id = ANY(:project)
  AND component_id IS NOT NULL
GROUP BY component_id
