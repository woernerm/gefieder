-- The schema is named after the component, the role after CRUDMAN_DB_USER: a role shares
-- one namespace with every other in the cluster and may have to dodge a collision, a
-- schema does not. The same split applies to sqlmesh and grafana below.
CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION ${CRUDMAN_DB_USER};

GRANT ALL PRIVILEGES ON SCHEMA crudman TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO ${CRUDMAN_DB_USER};

-- Read, not write. The default privileges name the schema's owner in their FOR ROLE,
-- that being what creates the tables.
GRANT USAGE ON SCHEMA crudman TO ${SQLMESH_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA crudman TO ${SQLMESH_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${CRUDMAN_DB_USER} IN SCHEMA crudman GRANT SELECT ON TABLES TO ${SQLMESH_DB_USER};

-- Grafana reads, never writes, the analytics data: the per-tenant bronze schemas, silver
-- and gold. It must not see sqlmesh's internals -- the physical schemas behind the virtual
-- layer, the staging schema and the state schema -- which hold churning objects not meant
-- to be queried.
--
-- create_tenant creates the bronze schemas later, so an event trigger grants each as it
-- appears, and only bronze_<tenant>, skipping sqlmesh__bronze_* and everything else
-- sqlmesh creates. silver and gold are created below and granted directly.
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
        -- The tenant bronze schemas, not sqlmesh's internal mirror of them. starts_with
        -- rather than LIKE: the configurable prefix ends in an underscore, which LIKE
        -- would read as a wildcard.
        CONTINUE WHEN NOT starts_with(obj.object_identity, '${BRONZE_SCHEMA_PREFIX}')
                   OR starts_with(obj.object_identity, 'sqlmesh__');

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

-- Grafana also reads the crudman model tables, but not Django's own auth_/django_ ones,
-- which hold credentials and framework state. The schema already exists, so USAGE is
-- granted here and an event trigger grants SELECT on every model table created later.
GRANT USAGE ON SCHEMA crudman TO ${GRAFANA_DB_USER};

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
        -- Django's own tables, and the dropzone table for its upload-link tokens; the
        -- upload and file tables the dashboards need stay readable.
        IF obj.object_identity LIKE 'crudman.auth\_%'
           OR obj.object_identity LIKE 'crudman.django\_%'
           OR obj.object_identity = 'crudman.dropzones_dropzone' THEN
            CONTINUE;
        END IF;

        EXECUTE format('GRANT SELECT ON %s TO ${GRAFANA_DB_USER}', obj.object_identity);
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS grafana_read_on_create_crudman_table;
CREATE EVENT TRIGGER grafana_read_on_create_crudman_table
    ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE')
    EXECUTE FUNCTION grant_grafana_read_crudman();
