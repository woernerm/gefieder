-- Bronze for the "Project B" tenant.
--
-- In a real deployment a bronze model is a VIEW over a shared raw source schema, selecting
-- the columns this tenant needs and filtering to its rows, so the raw data is present in
-- its bronze schema without being copied. It can also be a real table where the tenant has
-- a bespoke source. See models/bronze/README.md.
--
-- This example uses a SEED instead, so the pipeline has data out of the box. The raw
-- columns are GitHub-flavoured where Project A's are Jira-flavoured, which is why each
-- tenant needs its own bronze -> silver transform.
MODEL (
  name bronze_project_b.issues,
  kind SEED (
    path '../../../seeds/project_b_issues.csv'
  ),
  columns (
    id INTEGER,
    title TEXT,
    state TEXT,
    opened DATE,
    priority TEXT
  ),
  grain id
);
