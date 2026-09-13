-- Example gold model: precomputed issue metrics per project. Gold reads harmonized silver
-- only, so one set of gold models serves every project. Dashboards point at gold and silver,
-- never at the bronze or sqlmesh__* schemas.
--
-- Materialized (kind FULL): precomputed tables rather than views, so dashboards stay fast.
MODEL (
  name gold.issue_metrics,
  kind FULL,
  cron '@daily',
  grain tenant_id
);

SELECT
  tenant_id,
  COUNT(*)                                        AS total_issues,  -- Every issue the project ever opened.
  COUNT(*) FILTER (WHERE state = 'open')          AS open_issues,
  COUNT(*) FILTER (WHERE state = 'closed')        AS closed_issues,
  SUM(effort)                                     AS total_effort   -- In each project's own unit, so comparable within a project only.
FROM silver.issues
GROUP BY tenant_id
