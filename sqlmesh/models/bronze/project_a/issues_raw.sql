-- Bronze for the "Project A" tenant.
--
-- In a real deployment a bronze model is a VIEW over a shared raw source schema (jira,
-- sap, alm, github, ...) that selects only the columns this tenant needs and filters to
-- the rows that belong to it -- so the raw data is officially present in the tenant's
-- bronze schema without being copied. A bronze model can also be a real table when the
-- tenant has a bespoke source nobody else uses. See models/bronze/README.md.
--
-- This example uses a SEED instead, so the pipeline has data out of the box without any
-- external source. Note the Jira-flavoured raw column names; "Project B" looks completely
-- different, which is why the bronze -> silver transform is kept per tenant.
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
