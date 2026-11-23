-- Part of the silver harmonization contract: every row a staging model emits carries a
-- non-null tenant_id and issue_id, so a transform that forgets the canonical key columns
-- fails the plan rather than producing unattributable rows in silver.issues. tenant_id
-- names the project the row came from; it is a convention, not a boundary.
AUDIT (
  name assert_known_tenant
);

SELECT *
FROM @this_model
WHERE tenant_id IS NULL OR issue_id IS NULL
