-- The harmonized silver.issue_risk_history model: for every issue, the periods during
-- which its state, its effort and the safety classification and owning team of its
-- component all stayed the same. Built by silver/project_a/issue_risk_history.sql with
-- @temporal_join.
--
-- As thin as silver.issues, and for the same reason: every tenant-specific quirk is
-- resolved upstream, so adding a tenant is one UNION ALL line and gold never learns how
-- many tenants exist. Only project_a records a component history so far, which is why
-- there is a single SELECT here and no UNION ALL yet.
MODEL (
  name silver.issue_risk_history,
  kind VIEW,
  grain (tenant_id, issue_id, valid_from)
);

SELECT tenant_id, issue_id, valid_from, state, effort, component_id, safety_class, owner
FROM silver_staging.issue_risk_history__project_a
