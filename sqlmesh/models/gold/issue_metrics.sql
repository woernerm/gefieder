-- Example gold model: precomputed issue metrics per tenant. Gold reads the harmonized
-- silver schema only, so it needs no per-tenant logic at all -- one set of gold models
-- serves every tenant. Grafana dashboards point at gold (and silver), never at the
-- bronze schemas or the sqlmesh__* physical schemas.
--
-- Gold is materialized (kind FULL) per the medallion architecture: the metrics are
-- precomputed tables rather than views, so dashboards stay fast.
MODEL (
  name gold.issue_metrics,
  kind FULL,
  cron '@daily',
  grain tenant_id
);

SELECT
  tenant_id,
  COUNT(*)                                        AS total_issues,
  COUNT(*) FILTER (WHERE state = 'open')          AS open_issues,
  COUNT(*) FILTER (WHERE state = 'closed')        AS closed_issues,
  SUM(effort)                                     AS total_effort
FROM silver.issues
GROUP BY tenant_id
