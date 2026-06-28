-- Bronze for the "Project B" tenant.
--
-- In a real deployment a bronze model is a VIEW over a shared raw source schema (jira,
-- sap, alm, github, ...) that selects only the columns this tenant needs and filters to
-- the rows that belong to it -- so the raw data is officially present in the tenant's
-- bronze schema without being copied. A bronze model can also be a real table when the
-- tenant has a bespoke source nobody else uses. See models/bronze/README.md.
--
-- This example uses a SEED instead, so the pipeline has data out of the box without any
-- external source. The raw columns are GitHub-flavoured and differ from Project A's
-- Jira-flavoured ones, which is why each tenant needs its own bronze -> silver transform.
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
