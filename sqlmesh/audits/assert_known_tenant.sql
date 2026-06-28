-- Part of the silver harmonization contract: every row a staging model emits must carry a
-- non-null tenant_id and issue_id. Each per-tenant staging model references this audit, so
-- a transform that forgets to set the canonical key columns fails the plan instead of
-- silently producing unattributable rows in silver.issues.
AUDIT (
  name assert_known_tenant
);

SELECT *
FROM @this_model
WHERE tenant_id IS NULL OR issue_id IS NULL
