-- Project B's issue history: one row per change of an issue, the shape
-- bronze_project_a.issue_history carries under this project's own column names --
-- GitHub-flavoured, an issue being a number and an "area" label standing in for the
-- component.
--
-- The uniqueness audit is a precondition of the silver transform: it looks up the row in
-- effect at a point in time, and two rows sharing a timestamp would make that arbitrary.
MODEL (
  name bronze_project_b.issue_history,
  kind SEED (
    path '../../../seeds/project_b_issue_history.csv'
  ),
  columns (
    id INTEGER,
    updated DATE,
    state TEXT,
    priority TEXT,
    area TEXT
  ),
  column_descriptions (
    updated = 'The day this row took effect; it holds until the next row of the issue.',
    area = 'The area label, which is what stands in for a component here.'
  ),
  grain (id, updated),
  audits (unique_combination_of_columns(columns := (id, updated)))
);
