-- Project A's issue *history*: one row per change of an issue, which is what any question
-- about how a project developed has to be answered from. Issue trackers keep it alongside
-- the current state; Jira calls it the changelog.
--
-- A SEED like the other example models, so the pipeline has data without an external
-- source. The column names are the tenant's own, as always in bronze.
--
-- The uniqueness audit is not decoration: silver joins this with @temporal_join, which
-- reads the row in effect at a point in time, and two rows sharing a timestamp would make
-- that arbitrary.
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
