-- The harmonized silver.issues model: one canonical issue shape for every tenant.
--
-- This model is deliberately thin. All it does is stack the per-tenant transforms on top
-- of each other; every tenant-specific quirk has already been resolved upstream. Adding or
-- removing a tenant is the only reason to touch this file -- add one UNION ALL line per
-- tenant. Because each upstream model already emits the canonical columns, downstream gold
-- models never need to know how many tenants exist or how their raw data looked.
--
-- The upstream model differs by tenant only in *where* the bronze -> canonical transform
-- happens: project_a/project_b do it in a SQL staging model (silver_staging.issues__<tenant>),
-- project_c does it in a polars Python model that already lands canonical columns in its
-- bronze schema (see models/bronze/project_c/issues_raw.py). Either way the columns match.
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
