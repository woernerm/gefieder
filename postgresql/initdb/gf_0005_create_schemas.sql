-- The schema is named after the component, the role after CRUDMAN_DB_USER in
-- buildtime.env (postgresql/render.sh substituted it): a role shares one namespace with
-- every other role in the cluster and may have to dodge a collision, a schema does not.
-- The same split applies to the sqlmesh and grafana roles below.
CREATE SCHEMA IF NOT EXISTS crudman AUTHORIZATION ${CRUDMAN_DB_USER};

GRANT ALL PRIVILEGES ON SCHEMA crudman TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON TABLES TO ${CRUDMAN_DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA crudman GRANT ALL ON SEQUENCES TO ${CRUDMAN_DB_USER};

-- The analytics role may read, but not write, the crudman schema. The default privileges
-- name the schema's owner in their FOR ROLE, because that is what creates the tables.
GRANT USAGE ON SCHEMA crudman TO ${SQLMESH_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA crudman TO ${SQLMESH_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${CRUDMAN_DB_USER} IN SCHEMA crudman GRANT SELECT ON TABLES TO ${SQLMESH_DB_USER};

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
        -- Only the tenant bronze schemas are visible to grafana, and not sqlmesh's
        -- physical mirror of them (sqlmesh__bronze_*), which is internal. starts_with
        -- rather than LIKE because the prefix is configurable and ends in an
        -- underscore, which LIKE would read as a single-character wildcard.
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

CREATE EVENT TRIGGER grafana_read_on_create_schema
    ON ddl_command_end
    WHEN TAG IN ('CREATE SCHEMA')
    EXECUTE FUNCTION grant_grafana_read();

-- The standardized silver schema and the materialized gold schema are owned by
-- sqlmesh, which writes its models there. Grant grafana read on them directly (the event
-- trigger above only handles the bronze schemas). The default privileges are set FOR
-- sqlmesh so grafana can also read tables and views sqlmesh adds to them later.
CREATE SCHEMA IF NOT EXISTS ${SILVER_SCHEMA} AUTHORIZATION ${SQLMESH_DB_USER};
CREATE SCHEMA IF NOT EXISTS ${GOLD_SCHEMA} AUTHORIZATION ${SQLMESH_DB_USER};

GRANT USAGE ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${GRAFANA_DB_USER};
GRANT SELECT ON ALL TABLES IN SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${GRAFANA_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${SILVER_SCHEMA} GRANT SELECT ON TABLES TO ${GRAFANA_DB_USER};
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${GOLD_SCHEMA} GRANT SELECT ON TABLES TO ${GRAFANA_DB_USER};

-- Grafana may also read the crudman model tables, but not the Django-internal tables
-- (user, session, migration, ... tables, recognisable by their auth_/django_ prefix)
-- which hold credentials and framework state. The crudman schema already exists, so
-- grafana is granted USAGE here and an event trigger grants SELECT on every model
-- table crudman creates afterwards.
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
        -- Skip Django's own tables, and the dropzone table because it holds the
        -- secret upload-link tokens (grafana keeps read access to the upload/file
        -- tables, which are the ones dashboards need).
        IF obj.object_identity LIKE 'crudman.auth\_%'
           OR obj.object_identity LIKE 'crudman.django\_%'
           OR obj.object_identity = 'crudman.dropzones_dropzone' THEN
            CONTINUE;
        END IF;

        EXECUTE format('GRANT SELECT ON %s TO ${GRAFANA_DB_USER}', obj.object_identity);
    END LOOP;
END;
$$;

CREATE EVENT TRIGGER grafana_read_on_create_crudman_table
    ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE')
    EXECUTE FUNCTION grant_grafana_read_crudman();
