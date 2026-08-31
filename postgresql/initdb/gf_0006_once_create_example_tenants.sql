-- Three example tenants, so a fresh system has something to look at and the SQLMesh
-- project shows where real tenant files go. The "once" in the filename keeps this out of
-- the scripts the entrypoint re-applies, so deleting an example tenant is permanent.
--
-- They are ordinary tenants an administrator can delete in crudman like any other, which
-- leaves their model files behind for the admin to remove by hand.
--
-- The example password is deliberately simple and well known: these tenants exist to be
-- explored and then deleted. Nothing logs in to fill the bronze data, which comes from
-- the SEED and Python models reading the CSVs in seeds/.
SELECT create_tenant('project_a', 'changeme123', 'Project A');
SELECT create_tenant('project_b', 'changeme123', 'Project B');
SELECT create_tenant('project_c', 'changeme123', 'Project C');
