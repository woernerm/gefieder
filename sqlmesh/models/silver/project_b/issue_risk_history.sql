-- The same history as models/silver/project_a/issue_risk_history.sql -- an issue's own
-- changes combined with those of the component it belongs to -- and the same macro. What
-- differs is one line: `gateway duckdb`.
--
-- @temporal_join reads that gateway and emits the lookup its engine has. project_a gets
-- PostgreSQL's LATERAL ... ORDER BY ... LIMIT 1; this model gets DuckDB's ASOF JOIN, which
-- says the same thing in one line and executes as a single merge instead of a seek per
-- tick. `dialect duckdb` is what makes that legal to write down, and the macro refuses the
-- combination if it is missing -- ASOF is DuckDB grammar, and other dialects read the word
-- as a table alias rather than rejecting it.
--
-- The storage is PostgreSQL either way: the gateway attaches this database as its only
-- catalog (see config.py), so the bronze seeds are read from it and this table is written
-- back into it. The silver union downstream cannot tell which engine produced which half.
--
-- The status mapping stays one-to-one for the reason project_a's does: the macro judges
-- "unchanged" by the source columns, so folding "closed" and "merged" onto one canonical
-- state would let a source change through as a second row identical to the first. The
-- vocabulary is the same todo/in_progress/closed the other tenants use, and this tenant's
-- tool simply never reports in_progress.
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
