CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION crudman;

GRANT ALL PRIVILEGES ON SCHEMA crudman TO crudman;
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO crudman;
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO crudman;

-- The sqlmesh user may read, but not write, the crudman schema. The default
-- privileges are set FOR ROLE crudman because crudman creates the tables.
GRANT USAGE ON SCHEMA crudman TO sqlmesh;
GRANT SELECT ON ALL TABLES IN SCHEMA crudman TO sqlmesh;
ALTER DEFAULT PRIVILEGES FOR ROLE crudman IN SCHEMA crudman GRANT SELECT ON TABLES TO sqlmesh;

-- Grafana reads, but never writes, the analytics data. The silver and gold schemas
-- are created by sqlmesh at runtime, the per-tenant bronze schemas by create_tenant,
-- so an event trigger grants grafana read access to every schema as it is created.
-- The default privileges are set FOR the creating roles so that grafana can also
-- read tables and views added to those schemas later.
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
        -- Skip Django's own tables.
        IF obj.object_identity LIKE 'crudman.auth\_%'
           OR obj.object_identity LIKE 'crudman.django\_%' THEN
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
