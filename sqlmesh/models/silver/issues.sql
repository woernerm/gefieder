-- The harmonized silver.issues model: one canonical issue shape for every tenant.
--
-- This model is deliberately thin. All it does is stack the per-tenant staging models on
-- top of each other; every tenant-specific quirk has already been resolved upstream in
-- silver_staging.issues__<tenant>. Adding or removing a tenant is the only reason to touch
-- this file -- add one UNION ALL line per tenant. Because each staging model already emits
-- the canonical columns, downstream gold models never need to know how many tenants exist
-- or how their raw data looked.
MODEL (
  name silver.issues,
  kind VIEW,
  grain (tenant_id, issue_id)
);

SELECT tenant_id, issue_id, title, state, created_on, effort
FROM silver_staging.issues__project_a
UNION ALL
SELECT tenant_id, issue_id, title, state, created_on, effort
FROM silver_staging.issues__project_b
