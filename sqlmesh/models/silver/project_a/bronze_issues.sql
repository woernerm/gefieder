-- Example raw data for the "Project A" tenant. In a real deployment the bronze schema
-- is filled by external acquisition tools (or by views over a shared source schema such
-- as jira/sap/...), not by a SQLMesh seed. This seed only exists so the example pipeline
-- has something to harmonize out of the box. Note the Jira-flavoured raw column names;
-- "Project B" looks completely different, which is the whole point of keeping the
-- bronze -> silver transform per tenant.
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
