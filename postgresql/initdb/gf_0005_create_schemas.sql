CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION crudman;

GRANT ALL PRIVILEGES ON SCHEMA crudman TO crudman;
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO crudman;
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO crudman;

-- The sqlmesh user may read, but not write, the crudman schema. The default
-- privileges are set FOR ROLE crudman because crudman creates the tables.
GRANT USAGE ON SCHEMA crudman TO sqlmesh;
GRANT SELECT ON ALL TABLES IN SCHEMA crudman TO sqlmesh;
ALTER DEFAULT PRIVILEGES FOR ROLE crudman IN SCHEMA crudman GRANT SELECT ON TABLES TO sqlmesh;
