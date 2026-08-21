-- Project B's issue history: one row per change of an issue, the same shape Project A's
-- bronze_project_a.issue_history carries and, as always in bronze, under this tenant's own
-- column names -- GitHub-flavoured here, where an issue is a number and an "area" label
-- stands in for the component.
--
-- The uniqueness audit is the precondition the silver transform depends on: it looks up
-- the row in effect at a point in time, and two rows sharing a timestamp would make which
-- one arbitrary.
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
