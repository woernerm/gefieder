-- Project B's issue history: one row per change of an issue, the shape
-- bronze_project_a.issue_history carries under this tenant's own column names --
-- GitHub-flavoured, an issue being a number and an "area" label standing in for the
-- component.
--
-- The uniqueness audit is the silver transform's precondition: it looks up the row in
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
  grain (id, updated),
  audits (unique_combination_of_columns(columns := (id, updated)))
);
