-- Project B's component history: how each area's safety classification and owning team
-- changed over time. The counterpart of bronze_project_a.component_history, on its own
-- timeline again -- an area is reclassified when the safety case demands it, which has
-- nothing to do with when the issues on it are touched.
MODEL (
  name bronze_project_b.component_history,
  kind SEED (
    path '../../../seeds/project_b_component_history.csv'
  ),
  columns (
    area TEXT,
    updated DATE,
    classification TEXT,
    team TEXT
  ),
  grain (area, updated),
  audits (unique_combination_of_columns(columns := (area, updated)))
);
