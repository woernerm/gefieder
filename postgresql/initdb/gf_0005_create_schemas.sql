-- The schema is named after the component, the role after CRUDMAN_DB_USER: a role shares
-- one namespace with every other in the cluster and may have to dodge a collision, a
-- schema does not. The same split applies to sqlmesh and the dashboards below.
CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION ${CRUDMAN_DB_USER};

GRANT ALL PRIVILEGES ON SCHEMA crudman TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO ${CRUDMAN_DB_USER};

-- Read, not write. The default privileges name the schema's owner in their FOR ROLE,
-- that being what creates the tables.
GRANT USAGE ON SCHEMA crudman TO ${SQLMESH_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA crudman TO ${SQLMESH_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${CRUDMAN_DB_USER} IN SCHEMA crudman GRANT SELECT ON TABLES TO ${SQLMESH_DB_USER};

-- The dashboards read, never write, the analytics data: the per-project bronze schemas, silver
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
CREATE OR REPLACE FUNCTION grant_dashboards_read()
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

        EXECUTE format('GRANT USAGE ON SCHEMA %I TO ${DASHBOARDS_DB_USER}', obj.object_identity);
        EXECUTE format(
            'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO ${DASHBOARDS_DB_USER}', obj.object_identity
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO ${DASHBOARDS_DB_USER}',
            (SELECT nspowner::regrole FROM pg_namespace WHERE nspname = obj.object_identity),
            obj.object_identity
        );
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS dashboards_read_on_create_schema;
CREATE EVENT TRIGGER dashboards_read_on_create_schema
    ON ddl_command_end
    WHEN TAG IN ('CREATE SCHEMA')
    EXECUTE FUNCTION grant_dashboards_read();

-- Owned by sqlmesh, which writes its models there, and granted directly since the trigger
-- above handles only bronze. The default privileges are FOR sqlmesh, so the dashboards also
-- read what it adds later.
CREATE SCHEMA IF NOT EXISTS ${SILVER_SCHEMA} AUTHORIZATION ${SQLMESH_DB_USER};
CREATE SCHEMA IF NOT EXISTS ${GOLD_SCHEMA} AUTHORIZATION ${SQLMESH_DB_USER};

GRANT USAGE ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${DASHBOARDS_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${DASHBOARDS_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${SILVER_SCHEMA} GRANT SELECT ON TABLES TO ${DASHBOARDS_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${GOLD_SCHEMA} GRANT SELECT ON TABLES TO ${DASHBOARDS_DB_USER};

-- The dashboards also read the crudman model tables, but not Django's own auth_/django_ ones,
-- which hold credentials and framework state. The schema already exists, so USAGE is
-- granted here and an event trigger grants SELECT on every model table created later.
GRANT USAGE ON SCHEMA crudman TO ${DASHBOARDS_DB_USER};

CREATE OR REPLACE FUNCTION grant_dashboards_read_crudman()
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
        -- Django's own tables, and the dropzone table for its upload-link tokens; the
        -- upload and file tables the dashboards need stay readable.
        IF obj.object_identity LIKE 'crudman.auth\_%'
           OR obj.object_identity LIKE 'crudman.django\_%'
           OR obj.object_identity = 'crudman.dropzones_dropzone' THEN
            CONTINUE;
        END IF;

        EXECUTE format('GRANT SELECT ON %s TO ${DASHBOARDS_DB_USER}', obj.object_identity);
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS dashboards_read_on_create_crudman_table;
CREATE EVENT TRIGGER dashboards_read_on_create_crudman_table
    ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE')
    EXECUTE FUNCTION grant_dashboards_read_crudman();
