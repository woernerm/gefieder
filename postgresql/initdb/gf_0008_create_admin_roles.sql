-- The group roles behind per-admin database users.
--
-- Every administrator who needs SQL access gets a login role of their own (create_db_user
-- in gf_0003) rather than sharing the sqlmesh secret. Nothing is granted to it directly:
-- it is a member of exactly one of the three group roles below, so changing what an editor
-- may do is one GRANT here rather than a loop over every person.
--
-- The three names are the single sign-on group names of crudman/app/sso/roles.py, both
-- built from ROLE_PREFIX, so the rank the identity provider sends decides the database
-- rights too. NOINHERIT is deliberately not set: a member gets the rights by connecting.
--
-- A fourth role, <prefix>person, grants nothing: it is the marker create_db_user puts on
-- every account it provisions, and the only thing that tells a personal role from a tenant
-- or a service role. See is_db_user in gf_0003.

-- CREATE ROLE has no IF NOT EXISTS, and these scripts re-run on every start. The rights
-- below are re-granted either way, which is what lets a re-run repair a tampered grant.
DO $$
DECLARE
    group_role text;
BEGIN
    FOREACH group_role IN ARRAY ARRAY['${ROLE_PREFIX}viewer',
                                      '${ROLE_PREFIX}editor',
                                      '${ROLE_PREFIX}admin',
                                      '${ROLE_PREFIX}person']
    LOOP
        -- create_db_user would hand out somebody else's login role as one of ours, and
        -- an unprefixed "admin" is the cluster superuser.
        IF EXISTS (SELECT 1 FROM pg_roles
                   WHERE rolname = group_role AND (rolsuper OR rolcanlogin)) THEN
            RAISE EXCEPTION '% is a login role already -- change ROLE_PREFIX', group_role;
        END IF;

        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = group_role) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', group_role);
        END IF;
    END LOOP;
END;
$$;

--------------------------------------------------------------------
-- Read access to the analytics data.
--
-- All three ranks read the same things: the medallion layers and the crudman model
-- tables. What separates them is write access to SQLMesh's working schemas below. The
-- grants mirror grafana's in gf_0005: what a dashboard may read and what a person may read
-- are one question here.
--------------------------------------------------------------------
GRANT USAGE ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${ROLE_PREFIX}viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${ROLE_PREFIX}viewer;
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${SILVER_SCHEMA} GRANT SELECT ON TABLES TO ${ROLE_PREFIX}viewer;
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${GOLD_SCHEMA} GRANT SELECT ON TABLES TO ${ROLE_PREFIX}viewer;

-- Minus the tables holding credentials or secret tokens, as for grafana: Django's own
-- auth_/django_ tables and the dropzone table with its upload-link tokens.
GRANT USAGE ON SCHEMA crudman TO ${ROLE_PREFIX}viewer;

-- Editors and admins are members of the viewer role rather than repeating its grants.
GRANT ${ROLE_PREFIX}viewer TO ${ROLE_PREFIX}editor;
GRANT ${ROLE_PREFIX}editor TO ${ROLE_PREFIX}admin;

--------------------------------------------------------------------
-- Write access for SQLMesh development.
--
-- `sqlmesh plan` writes snapshots to the state schema and materialises models into the
-- physical schemas behind the virtual layer. Those are owned by sqlmesh and created as
-- models appear, so the event trigger below applies the grants.
--
-- This permits `sqlmesh plan prod` too: promoting is a view swap in exactly the schemas a
-- developer must write to in order to plan at all, and separating the two would cost a
-- state and physical layer per person. The control is that production deploys from CI.
--------------------------------------------------------------------
GRANT CREATE ON DATABASE ${PG_DATABASE} TO ${ROLE_PREFIX}editor;

-- SQLMesh's schemas appear on its first plan and whenever a model lands in a new one, so
-- an event trigger grants each as it is created, as gf_0005 does for grafana.
CREATE OR REPLACE FUNCTION grant_developer_write()
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
        -- Only the schemas SQLMesh works in: its physical layer, its state schema and
        -- the medallion virtual layer, including the staging and per-environment copies a
        -- dev plan creates -- hence the prefix match. starts_with rather than LIKE, a
        -- configured name possibly holding an underscore.
        CONTINUE WHEN NOT starts_with(obj.object_identity, 'sqlmesh')
                  AND NOT starts_with(obj.object_identity, '${SILVER_SCHEMA}')
                  AND NOT starts_with(obj.object_identity, '${GOLD_SCHEMA}')
                  AND NOT starts_with(obj.object_identity, '${BRONZE_SCHEMA_PREFIX}');

        EXECUTE format('GRANT ALL ON SCHEMA %I TO ${ROLE_PREFIX}editor', obj.object_identity);
        EXECUTE format(
            'GRANT ALL ON ALL TABLES IN SCHEMA %I TO ${ROLE_PREFIX}editor', obj.object_identity
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT ALL ON TABLES TO ${ROLE_PREFIX}editor',
            (SELECT nspowner::regrole FROM pg_namespace WHERE nspname = obj.object_identity),
            obj.object_identity
        );
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS developer_write_on_create_schema;
CREATE EVENT TRIGGER developer_write_on_create_schema
    ON ddl_command_end
    WHEN TAG IN ('CREATE SCHEMA')
    EXECUTE FUNCTION grant_developer_write();

-- gf_0005 created these before the trigger existed.
GRANT ALL ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${ROLE_PREFIX}editor;

-- The engine has to read and replace what a developer's plan materialised, which it
-- cannot do for a table another role owns. Rather than widening the production role, every
-- developer's role gives sqlmesh default membership of what it creates (gf_0003).

--------------------------------------------------------------------
-- Who may provision database users.
--
-- PostgreSQL grants EXECUTE on a new function to PUBLIC, which for a SECURITY DEFINER
-- function that can CREATE ROLE would let every role in the cluster provision itself an
-- admin account. The functions live in gf_0003, but the grant waits until here because
-- gf_0003 runs before gf_0004 creates the crudman role.
--------------------------------------------------------------------
REVOKE ALL ON FUNCTION create_db_user(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION delete_db_user(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION clear_db_user_password(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION drop_db_user(text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION create_db_user(text, text, text) TO ${CRUDMAN_DB_USER};
GRANT EXECUTE ON FUNCTION delete_db_user(text) TO ${CRUDMAN_DB_USER};
GRANT EXECUTE ON FUNCTION clear_db_user_password(text) TO ${CRUDMAN_DB_USER};
GRANT EXECUTE ON FUNCTION drop_db_user(text) TO ${CRUDMAN_DB_USER};
