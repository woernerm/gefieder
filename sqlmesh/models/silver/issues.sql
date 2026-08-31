-- The harmonized silver.issues model: one canonical issue shape for every tenant.
--
-- Deliberately thin: it stacks the per-tenant transforms, every quirk having been resolved
-- upstream. Adding or removing a tenant is one UNION ALL line and the only reason to touch
-- this file, and gold never learns how many tenants exist.
--
-- The upstream model differs by tenant only in *where* the transform happens: project_a and
-- project_b use a SQL staging model, project_c a polars Python model that already lands
-- canonical columns in its bronze schema. Either way the columns match.
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
UNION ALL
SELECT tenant_id, issue_id, title, state, created_on, effort
FROM bronze_project_c.issues
