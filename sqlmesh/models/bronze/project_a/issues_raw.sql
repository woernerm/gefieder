-- Bronze for the "Project A" tenant.
--
-- In a real deployment a bronze model is a VIEW over a shared raw source schema, selecting
-- the columns this tenant needs and filtering to its rows, so the raw data is present in
-- its bronze schema without being copied. It can also be a real table where the tenant has
-- a bespoke source. See models/bronze/README.md.
--
-- This example uses a SEED instead, so the pipeline has data out of the box. The raw
-- column names are Jira-flavoured, where "Project B" looks completely different, which is
-- why the bronze -> silver transform is kept per tenant.
MODEL (
  name bronze_project_a.issues,
  kind SEED (
    path '../../../seeds/project_a_issues.csv'
  ),
  columns (
    issue_key TEXT,
    summary TEXT,
    status TEXT,
    created_at DATE,
    story_points INTEGER
  ),
  grain issue_key
);
