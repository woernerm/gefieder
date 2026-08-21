-- The harmonized silver.issue_risk_history model: for every issue, the periods during
-- which its state, its effort and the safety classification and owning team of its
-- component all stayed the same. Built by silver/project_{a,b}/issue_risk_history.sql
-- with @temporal_join.
--
-- As thin as silver.issues, and for the same reason: every tenant-specific quirk is
-- resolved upstream, so adding a tenant is one UNION ALL line and gold never learns how
-- many tenants exist. Here that goes one step further -- both halves come from the same
-- @temporal_join, but project_a's was executed by PostgreSQL and project_b's by DuckDB on
-- the duckdb gateway. This union cannot tell the difference: both wrote their table into
-- the same database, and the macro guarantees they mean the same thing.
MODEL (
  name silver.issue_risk_history,
  kind VIEW,
  grain (tenant_id, issue_id, valid_from)
);

SELECT tenant_id, issue_id, valid_from, state, effort, component_id, safety_class, owner
FROM silver_staging.issue_risk_history__project_a
UNION ALL
SELECT tenant_id, issue_id, valid_from, state, effort, component_id, safety_class, owner
FROM silver_staging.issue_risk_history__project_b
