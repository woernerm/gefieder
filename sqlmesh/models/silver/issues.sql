-- The harmonized silver.issues model: one canonical issue shape for every project.
--
-- Deliberately thin: it stacks the per-project transforms, every quirk having been resolved
-- upstream. Adding or removing a project is one UNION ALL line and the only reason to touch
-- this file, and gold never learns how many projects exist.
--
-- The upstream model differs by project only in *where* the transform happens: project_a and
-- project_b use a SQL staging model, project_c a polars Python model that already lands
-- canonical columns in its bronze schema. Either way the columns match.
--
-- The comments on the first SELECT are the columns' descriptions: SQLMesh registers them,
-- and a notebook shows them wherever this model is referenced.
MODEL (
  name silver.issues,
  kind VIEW,
  grain (tenant_id, issue_id)
);

SELECT
  tenant_id,  -- The project a row came from; a naming convention, nothing the database enforces.
  issue_id,   -- The issue's key in the project's own tracker, unique within the project.
  title,
  state,      -- 'open' or 'closed': every project's own workflow vocabulary is folded onto these two.
  created_on,
  effort      -- The project's estimate in its own unit: story points, a priority weight, a "weight" field.
FROM silver_staging.issues__project_a
UNION ALL
SELECT tenant_id, issue_id, title, state, created_on, effort
FROM silver_staging.issues__project_b
UNION ALL
SELECT tenant_id, issue_id, title, state, created_on, effort
FROM bronze_project_c.issues
