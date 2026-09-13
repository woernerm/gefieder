-- The bronze -> silver transform of Project A. Everything specific to how *this* project records
-- issues lives here: its Jira-style column names, and its own status vocabulary mapped onto
-- the canonical open/closed states that every silver output agrees on.
--
-- The output column list IS the harmonization contract; it must match the other staging
-- models and the silver.issues union exactly.
MODEL (
  name silver_staging.issues__project_a,
  kind FULL,
  grain (tenant_id, issue_id),
  audits (assert_known_tenant)
);

SELECT
  'project_a'                                AS tenant_id,  -- Fixed: every row of a staging model is its project's.
  issue_key                                  AS issue_id,
  summary                                    AS title,
  CASE
    WHEN status = 'Done' THEN 'closed'
    ELSE 'open'
  END                                        AS state,      -- Jira's Done is closed; every other status is open.
  created_at                                 AS created_on,
  story_points                               AS effort      -- Story points, the unit this team sizes in.
FROM bronze_project_a.issues
