CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION crudman;

GRANT ALL PRIVILEGES ON SCHEMA crudman TO crudman;
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO crudman;
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO crudman;

-- The sqlmesh user may read, but not write, the crudman schema. The default
-- privileges are set FOR ROLE crudman because crudman creates the tables.
GRANT USAGE ON SCHEMA crudman TO sqlmesh;
GRANT SELECT ON ALL TABLES IN SCHEMA crudman TO sqlmesh;
ALTER DEFAULT PRIVILEGES FOR ROLE crudman IN SCHEMA crudman GRANT SELECT ON TABLES TO sqlmesh;

-- Grafana reads, but never writes, the analytics data: the per-tenant bronze schemas
-- (bronze_<tenant>), the standardized silver schema and the materialized gold schema.
-- It must NOT see sqlmesh's internals: the physical schemas behind the virtual layer
-- (sqlmesh__*), the per-tenant silver staging schema (silver_staging) and the state
-- schema (sqlmesh) all hold versioned, churning objects that are not meant to be queried.
--
-- The bronze schemas are created later by create_tenant, so an event trigger grants
-- grafana read access as each one appears -- but only for bronze_<tenant> schemas, so the
-- sqlmesh__bronze_* physical schemas (and every other sqlmesh-created schema) are skipped.
-- silver and gold are created explicitly below and granted directly.
CREATE OR REPLACE FUNCTION grant_grafana_read()
RETURNS event_trigger
LANGUAGE plpgsql
AS $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT object_identity
        FROM pg_event_trigger_ddl_commands()
        WHERE command_tag = 'CREATE SCHEMA'
    LOOP
        -- Only the tenant bronze schemas are visible to grafana. Match bronze_% but
        -- exclude sqlmesh's physical mirror of them (sqlmesh__bronze_%), which is internal.
        CONTINUE WHEN obj.object_identity NOT LIKE 'bronze\_%'
                   OR obj.object_identity LIKE 'sqlmesh\_\_%';

        EXECUTE format('GRANT USAGE ON SCHEMA %I TO grafana', obj.object_identity);
        EXECUTE format(
            'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO grafana', obj.object_identity
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO grafana',
            (SELECT nspowner::regrole FROM pg_namespace WHERE nspname = obj.object_identity),
            obj.object_identity
        );
    END LOOP;
END;
$$;

CREATE EVENT TRIGGER grafana_read_on_create_schema
    ON ddl_command_end
    WHEN TAG IN ('CREATE SCHEMA')
    EXECUTE FUNCTION grant_grafana_read();

-- The standardized silver schema and the materialized gold schema are owned by
-- sqlmesh, which writes its models there. Grant grafana read on them directly (the event
-- trigger above only handles the bronze schemas). The default privileges are set FOR
-- sqlmesh so grafana can also read tables and views sqlmesh adds to them later.
CREATE SCHEMA IF NOT EXISTS silver AUTHORIZATION sqlmesh;
CREATE SCHEMA IF NOT EXISTS gold AUTHORIZATION sqlmesh;

GRANT USAGE ON SCHEMA silver, gold TO grafana;
GRANT SELECT ON ALL TABLES IN SCHEMA silver, gold TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE sqlmesh IN SCHEMA silver GRANT SELECT ON TABLES TO grafana;
ALTER DEFAULT PRIVILEGES FOR ROLE sqlmesh IN SCHEMA gold GRANT SELECT ON TABLES TO grafana;

-- Grafana may also read the crudman model tables, but not the Django-internal tables
-- (user, session, migration, ... tables, recognisable by their auth_/django_ prefix)
-- which hold credentials and framework state. The crudman schema already exists, so
-- grafana is granted USAGE here and an event trigger grants SELECT on every model
-- table crudman creates afterwards.
GRANT USAGE ON SCHEMA crudman TO grafana;

CREATE OR REPLACE FUNCTION grant_grafana_read_crudman()
RETURNS event_trigger
LANGUAGE plpgsql
AS $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT objid, object_identity
        FROM pg_event_trigger_ddl_commands()
        WHERE command_tag = 'CREATE TABLE'
          AND schema_name = 'crudman'
    LOOP
        -- Skip Django's own tables, and the dropzone table because it holds the
        -- secret upload-link tokens (grafana keeps read access to the upload/file
        -- tables, which are the ones dashboards need).
        IF obj.object_identity LIKE 'crudman.auth\_%'
           OR obj.object_identity LIKE 'crudman.django\_%'
           OR obj.object_identity = 'crudman.dropzones_dropzone' THEN
            CONTINUE;
        END IF;

        EXECUTE format('GRANT SELECT ON %s TO grafana', obj.object_identity);
    END LOOP;
END;
$$;

CREATE EVENT TRIGGER grafana_read_on_create_crudman_table
    ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE')
    EXECUTE FUNCTION grant_grafana_read_crudman();
