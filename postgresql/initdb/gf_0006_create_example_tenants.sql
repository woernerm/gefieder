-- Seed two example tenants so a fresh system has something to look at and so the SQLMesh
-- project (models/silver/project_a, models/silver/project_b) illustrates where real tenant
-- files go. initdb scripts run only once, when the data volume is first created, so this
-- does not interfere with tenants created later through the admin panel.
--
-- These are ordinary tenants: an administrator can delete them in crudman like any other.
-- (Deleting a tenant does not remove its SQLMesh .sql files; the admin removes the
-- corresponding models/silver/<slug> folder by hand.)
--
-- create_tenant is idempotent (it updates the password if the role already exists), so a
-- name clash with a real tenant called project_a/project_b would be harmless here.
--
-- The example login password is intentionally simple and well known: these tenants exist
-- to be explored and then deleted, so the password is documentation, not a secret. The
-- per-tenant bronze data is provided by SQLMesh SEED models, not by this role logging in.
SELECT create_tenant('project_a', 'changeme123');
SELECT create_tenant('project_b', 'changeme123');
