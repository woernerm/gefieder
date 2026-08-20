-- Project A's second bronze -> silver transform, and the worked example for
-- @temporal_join (macros/temporal_join.py): the history of an issue combined with the
-- history of the component it belongs to.
--
-- Both sides move on their own timeline. The issue is worked on; the component is
-- reclassified when the safety case changes. A plain ASOF join would follow only one of
-- them and silently miss every change of the other, so the macro builds the joined history
-- from the union of both timelines: a row here starts a period during which everything
-- this model reports stayed the same, which is what makes "how long did this issue sit on a
-- class D component" a question the data can answer.
--
-- The macro judges "stayed the same" by the source columns, which is why the status
-- mapping below is one-to-one and why every source column is reported. A mapping that
-- folded two statuses onto one canonical state would let a source change through as a
-- second row that looks exactly like the first -- so would leaving owner_team out. Both
-- are caught by assert_every_row_is_a_change rather than left to be noticed downstream:
-- a tenant that starts using a fourth status will fail that audit until the mapping is
-- extended, which is the point at which to extend it.
--
-- The vocabulary is the "closed" that silver.issues already uses, with its open half split
-- into the two states a history has to tell apart.
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
