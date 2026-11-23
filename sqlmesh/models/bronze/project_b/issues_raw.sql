-- Bronze for Project B.
--
-- In a real deployment a bronze model is a VIEW over a shared raw source schema, selecting
-- the columns this project needs and filtering to its rows, so the raw data is present in
-- its bronze schema without being copied. It can also be a real table where the project has
-- a bespoke source. See models/bronze/README.md.
--
-- This example uses a SEED instead, so the pipeline has data out of the box. The raw
-- columns are GitHub-flavoured where those of Project A are Jira-flavoured, which is why each
-- project needs its own bronze -> silver transform.
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
  column_descriptions (
    id = 'The GitHub issue number.',
    state = 'The state as GitHub records it: open, closed or merged.',
    priority = 'The priority label; Project B sizes nothing, so silver derives effort from it.'
  ),
  grain id
);
