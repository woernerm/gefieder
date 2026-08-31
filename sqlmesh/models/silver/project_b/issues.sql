-- Project B's bronze -> silver transform. This tenant's tool has no notion of story
-- points, so "effort" is derived from its priority field -- an example of one canonical
-- silver column filled from completely different raw data per tenant. Its status vocabulary
-- is its own and is mapped here onto the canonical open/closed states.
--
-- The output column list IS the harmonization contract; it must match the other tenants'
-- staging models and the silver.issues union exactly.
MODEL (
  name silver_staging.issues__project_b,
  kind FULL,
  grain (tenant_id, issue_id),
  audits (assert_known_tenant)
);

SELECT
  'project_b'                                AS tenant_id,
  id::TEXT                                   AS issue_id,
  title                                      AS title,
  CASE
    WHEN state IN ('closed', 'merged') THEN 'closed'
    ELSE 'open'
  END                                        AS state,
  opened                                     AS created_on,
  CASE priority
    WHEN 'high' THEN 8
    WHEN 'medium' THEN 5
    ELSE 2
  END                                        AS effort
FROM bronze_project_b.issues
