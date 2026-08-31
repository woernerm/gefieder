-- The same history and the same macro as models/silver/project_a/issue_risk_history.sql.
-- What differs is one line: `gateway duckdb`.
--
-- @temporal_join reads that gateway and emits the lookup its engine has: PostgreSQL's
-- LATERAL ... LIMIT 1 there, DuckDB's ASOF JOIN here, which executes as a single merge
-- rather than a seek per tick. `dialect duckdb` makes that legal to write down, and the
-- macro refuses the combination without it -- ASOF is DuckDB grammar, which another dialect
-- reads as a table alias rather than rejecting.
--
-- The storage is PostgreSQL either way: the gateway attaches this database as its only
-- catalog, so the silver union cannot tell which engine produced which half.
--
-- The status mapping stays one-to-one for the reason project_a's does; this tenant's tool
-- never reports in_progress.
MODEL (
  name silver_staging.issue_risk_history__project_b,
  kind FULL,
  gateway duckdb,
  dialect duckdb,
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
  'project_b'                                                               AS tenant_id,
  tck.id::TEXT                                                              AS issue_id,
  tck.updated                                                               AS valid_from,
  CASE WHEN lhs.state IN ('closed', 'merged') THEN 'closed' ELSE 'todo' END AS state,
  CASE lhs.priority WHEN 'high' THEN 8 WHEN 'medium' THEN 5 ELSE 2 END      AS effort,
  lhs.area                                                                  AS component_id,
  rhs.classification                                                        AS safety_class,
  rhs.team                                                                  AS owner
FROM @temporal_join(
  lhs_ts := bronze_project_b.issue_history.updated,
  rhs_ts := bronze_project_b.component_history.updated,
  key    := id,
  on     := (rhs.area = lhs.area)
)
