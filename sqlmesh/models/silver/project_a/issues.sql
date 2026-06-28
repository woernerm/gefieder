-- Project A's bronze -> silver transform. Everything specific to how *this* tenant
-- records issues lives here and nowhere else: its Jira-style column names, and its own
-- status vocabulary ("Done"/"In Progress"/"To Do") mapped onto the canonical
-- open/closed states that every tenant's silver output must agree on. A different tenant
-- (see project_b) decodes entirely different raw columns into the same shape.
--
-- The output column list IS the harmonization contract; it must match the other tenants'
-- staging models and the silver.issues union exactly.
MODEL (
  name silver_staging.issues__project_a,
  kind FULL,
  grain (tenant_id, issue_id),
  audits (assert_known_tenant)
);

SELECT
  'project_a'                                AS tenant_id,
  issue_key                                  AS issue_id,
  summary                                    AS title,
  CASE
    WHEN status = 'Done' THEN 'closed'
    ELSE 'open'
  END                                        AS state,
  created_at                                 AS created_on,
  story_points                               AS effort
FROM bronze_project_a.issues
