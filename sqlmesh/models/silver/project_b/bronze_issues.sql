-- Example raw data for the "Project B" tenant. As with Project A this seed stands in for
-- data that external tools would normally write into the bronze schema. The raw columns
-- are GitHub-flavoured and differ from Project A's Jira-flavoured ones, so each tenant
-- needs its own bronze -> silver transform in the staging model next to this file.
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
