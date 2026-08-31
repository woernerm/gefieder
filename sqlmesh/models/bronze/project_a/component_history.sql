-- Project A's component history: how each component's safety classification and owning
-- team changed over time. A second history on its own timeline, a component being
-- reclassified when the safety case demands it rather than when its issues are touched.
--
-- That is the point of the example: silver joins the two, and the joined history has to
-- follow both. See models/silver/project_a/issue_risk_history.sql.
MODEL (
  name bronze_project_a.component_history,
  kind SEED (
    path '../../../seeds/project_a_component_history.csv'
  ),
  columns (
    component_key TEXT,
    changed_at DATE,
    safety_class TEXT,
    owner_team TEXT
  ),
  grain (component_key, changed_at),
  audits (unique_combination_of_columns(columns := (component_key, changed_at)))
);
