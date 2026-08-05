-- Seed three example tenants so a fresh system has something to look at and so the SQLMesh
-- project (models/silver/project_a, models/silver/project_b and the polars-based
-- models/bronze/project_c) illustrates where real tenant files go. initdb scripts run only
-- once, when the data volume is first created, so this does not interfere with tenants
-- created later through the admin panel.
--
-- These are ordinary tenants: an administrator can delete them in crudman like any other.
-- (Deleting a tenant leaves its SQLMesh model files; the admin removes the tenant's
-- folder by hand: models/silver/<slug>, or models/bronze/<slug> for project_c.)
--
-- create_tenant is idempotent (it updates the password if the role already exists), so a
-- name clash with a real tenant called project_a/project_b would be harmless here.
--
-- The example login password is intentionally simple and well known: these tenants exist
-- to be explored and then deleted, so the password is documentation, not a secret. No
-- role logs in to fill the bronze data: it comes from SQLMesh SEED models, and for
-- project_c a polars Python model, all reading the CSVs in seeds/.
SELECT create_tenant('project_a', 'changeme123', 'Project A');
SELECT create_tenant('project_b', 'changeme123', 'Project B');
SELECT create_tenant('project_c', 'changeme123', 'Project C');
