-- Project A's issue *history*: one row per change of an issue, not one row per issue.
-- Issue trackers keep this alongside the current state (Jira calls it the changelog), and
-- it is what any question about how a project developed has to be answered from.
--
-- Like the other example models this is a SEED so the pipeline has data without an
-- external source; a real tenant's bronze model is a view over the source schema. The
-- column names are the tenant's own, as always in bronze.
--
-- The uniqueness audit is not decoration: silver joins this history to another one with
-- @temporal_join, which reads the row in effect at a point in time. Two rows with the same
-- timestamp for one issue would make "the row in effect" arbitrary.
MODEL (
  name bronze_project_a.issue_history,
  kind SEED (
    path '../../../seeds/project_a_issue_history.csv'
  ),
  columns (
    issue_key TEXT,
    changed_at DATE,
    status TEXT,
    story_points INTEGER,
    component_key TEXT
  ),
  grain (issue_key, changed_at),
  audits (unique_combination_of_columns(columns := (issue_key, changed_at)))
);
