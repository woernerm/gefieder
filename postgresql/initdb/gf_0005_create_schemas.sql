-- The schema is named after the component, the role after CRUDMAN_DB_USER: a role shares
-- one namespace with every other in the cluster and may have to dodge a collision, a
-- schema does not. The same split applies to sqlmesh and grafana below.
CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION ${CRUDMAN_DB_USER};

GRANT ALL PRIVILEGES ON SCHEMA crudman TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO ${CRUDMAN_DB_USER};

-- Read, not write, and no table holding a credential or a session: whoever may ship a model
-- may read what this role reads. Table by table below, hence no default privilege.
GRANT USAGE ON SCHEMA crudman TO ${SQLMESH_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${CRUDMAN_DB_USER} IN SCHEMA crudman REVOKE SELECT ON TABLES FROM ${SQLMESH_DB_USER};

-- Grafana reads, never writes, the analytics data: the per-project bronze schemas, silver
-- and gold. It must not see sqlmesh's internals -- the physical schemas behind the virtual
-- layer, the staging schema and the state schema -- which hold churning objects not meant
-- to be queried.
--
-- SQLMesh creates a bronze schema when a model first names one, so an event trigger grants
-- each as it appears, and only the medallion layers, skipping sqlmesh__* and everything
-- else sqlmesh creates. silver and gold are created below and granted directly.
--
-- A plan into an environment of its own -- what a version under review is deployed as --
-- names its schemas <layer>__<environment>, so the layer is read off the part before the
-- separator and each copy is granted like the layer it copies. Without it the preview
-- database, which is foreign tables over exactly those schemas, would read nothing.
CREATE OR REPLACE FUNCTION grant_grafana_read()
RETURNS event_trigger
LANGUAGE plpgsql
AS $$
DECLARE
    obj record;
    layer text;
BEGIN
    FOR obj IN
        SELECT object_identity
        FROM pg_event_trigger_ddl_commands()
        WHERE command_tag = 'CREATE SCHEMA'
    LOOP
        layer := split_part(obj.object_identity, '__', 1);

        -- The medallion layers, not sqlmesh's internal mirror of them and not the staging
        -- schema, whose name merely starts like silver's. starts_with rather than LIKE:
        -- the configurable prefix ends in an underscore, which LIKE would read as a
        -- wildcard.
        CONTINUE WHEN NOT starts_with(layer, '${BRONZE_SCHEMA_PREFIX}')
                  AND layer NOT IN ('${SILVER_SCHEMA}', '${GOLD_SCHEMA}');

        EXECUTE format('GRANT USAGE ON SCHEMA %I TO ${GRAFANA_DB_USER}', obj.object_identity);
        EXECUTE format(
            'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO ${GRAFANA_DB_USER}', obj.object_identity
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO ${GRAFANA_DB_USER}',
            (SELECT nspowner::regrole FROM pg_namespace WHERE nspname = obj.object_identity),
            obj.object_identity
        );
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS grafana_read_on_create_schema;
CREATE EVENT TRIGGER grafana_read_on_create_schema
    ON ddl_command_end
    WHEN TAG IN ('CREATE SCHEMA')
    EXECUTE FUNCTION grant_grafana_read();

-- Owned by sqlmesh, which writes its models there, and granted directly since the trigger
-- above handles only bronze. The default privileges are FOR sqlmesh, so grafana also reads
-- what it adds later.
CREATE SCHEMA IF NOT EXISTS ${SILVER_SCHEMA} AUTHORIZATION ${SQLMESH_DB_USER};
CREATE SCHEMA IF NOT EXISTS ${GOLD_SCHEMA} AUTHORIZATION ${SQLMESH_DB_USER};

GRANT USAGE ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${GRAFANA_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${GRAFANA_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${SILVER_SCHEMA} GRANT SELECT ON TABLES TO ${GRAFANA_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${GOLD_SCHEMA} GRANT SELECT ON TABLES TO ${GRAFANA_DB_USER};

-- Which crudman tables the analytics side must not read. Of the tables this system ships
-- itself, all but the named ones, so one added later is private until somebody decides
-- otherwise. A table of an app added to this system is readable, that being what it is for.
CREATE OR REPLACE FUNCTION public.is_private_crudman_table(table_name text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog
AS $$
    SELECT table_name ~ '^(account|auth|dbusers|django|dropzones|shell|socialaccount|system)_'
       AND table_name NOT IN ('dropzones_upload', 'dropzones_uploadfile', 'system_deployment');
$$;

-- What an earlier release granted, and sqlmesh's read access to what exists already.
DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT oid::regclass AS identity, relname
        FROM pg_class
        WHERE relnamespace = 'crudman'::regnamespace AND relkind IN ('r', 'p')
    LOOP
        IF public.is_private_crudman_table(obj.relname) THEN
            EXECUTE format(
                'REVOKE ALL ON %s FROM ${GRAFANA_DB_USER}, ${SQLMESH_DB_USER}', obj.identity
            );
        ELSE
            EXECUTE format('GRANT SELECT ON %s TO ${SQLMESH_DB_USER}', obj.identity);
        END IF;
    END LOOP;
END;
$$;

-- The schema already exists, so USAGE is granted here and an event trigger grants SELECT on
-- every readable table created later.
GRANT USAGE ON SCHEMA crudman TO ${GRAFANA_DB_USER};

CREATE OR REPLACE FUNCTION grant_crudman_read()
RETURNS event_trigger
LANGUAGE plpgsql
AS $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT c.oid::regclass AS identity, c.relname
        FROM pg_event_trigger_ddl_commands() ddl
        JOIN pg_class c ON c.oid = ddl.objid
        WHERE ddl.command_tag = 'CREATE TABLE'
          AND ddl.schema_name = 'crudman'
    LOOP
        CONTINUE WHEN public.is_private_crudman_table(obj.relname);

        EXECUTE format(
            'GRANT SELECT ON %s TO ${GRAFANA_DB_USER}, ${SQLMESH_DB_USER}', obj.identity
        );
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS grafana_read_on_create_crudman_table;
DROP FUNCTION IF EXISTS grant_grafana_read_crudman();
DROP EVENT TRIGGER IF EXISTS read_on_create_crudman_table;
CREATE EVENT TRIGGER read_on_create_crudman_table
    ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE')
    EXECUTE FUNCTION grant_crudman_read();
