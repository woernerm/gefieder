-- Project A's second bronze -> silver transform, and the worked example for @temporal_join
-- (macros/temporal_join.py): an issue's history combined with its component's.
--
-- Both sides move on their own timeline, so a plain ASOF join would follow one and miss
-- every change of the other. The macro builds the joined history from the union of both, so
-- a row starts a period during which everything this model reports stayed the same -- which
-- is what makes "how long did this issue sit on a class D component" answerable.
--
-- "Stayed the same" is judged by the source columns, which is why the status mapping is
-- one-to-one and every source column is reported: folding two statuses into one, or leaving
-- owner_team out, would let a source change through as a row identical to the one before.
-- assert_every_row_is_a_change catches both.
--
-- The vocabulary is the "closed" silver.issues uses, its open half split into the two
-- states a history has to tell apart.
MODEL (
  name silver_staging.issue_risk_history__project_a,
  kind FULL,
  grain (tenant_id, issue_id, valid_from),
  audits (
    assert_known_tenant,
    unique_combination_of_columns(columns := (issue_id, valid_from)),
    assert_every_row_is_a_change(
      key := issue_id,
      ts := valid_from,
      payload := (state, effort, component_id, safety_class, owner)
    )
  )
);

SELECT
  'project_a'         AS tenant_id,
  tck.issue_key       AS issue_id,
  tck.changed_at      AS valid_from,
  CASE lhs.status
    WHEN 'Done'        THEN 'closed'
    WHEN 'In Progress' THEN 'in_progress'
    ELSE 'todo'
  END                 AS state,
  lhs.story_points    AS effort,
  lhs.component_key   AS component_id,
  rhs.safety_class    AS safety_class,
  rhs.owner_team      AS owner
FROM @temporal_join(
  lhs_ts := bronze_project_a.issue_history.changed_at,
  rhs_ts := bronze_project_a.component_history.changed_at,
  key    := issue_key,
  on     := (rhs.component_key = lhs.component_key)
)
