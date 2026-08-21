-- The group roles behind per-admin database users.
--
-- Every administrator who needs SQL access gets a login role of their own (created by
-- crudman, see create_db_user in gf_0003) rather than sharing the sqlmesh secret. What
-- such a role may do is not granted to it directly: it is a member of exactly one of the
-- three group roles below, which carry the privileges. That indirection is the point --
-- changing what an editor may do is one GRANT here, not a loop over every person.
--
-- The three names mirror the single sign-on groups in crudman/app/sso/roles.py
-- (sso-viewer, sso-editor, sso-admin), so the rank the identity provider sends decides
-- the database rights too, and there is one place where that mapping is written down.
--
-- NOINHERIT is deliberately NOT set: a member should get the group's rights simply by
-- connecting, without having to SET ROLE.

CREATE ROLE ${DB_ROLE_PREFIX}viewer NOLOGIN;
CREATE ROLE ${DB_ROLE_PREFIX}editor NOLOGIN;
CREATE ROLE ${DB_ROLE_PREFIX}admin NOLOGIN;

--------------------------------------------------------------------
-- Read access to the analytics data.
--
-- All three ranks may read the same things: the medallion layers and the crudman model
-- tables. What separates them is write access to SQLMesh's working schemas below. The
-- grants mirror grafana's in gf_0005 -- deliberately, because "what a dashboard may
-- read" and "what a person may read" are the same question here.
--------------------------------------------------------------------
GRANT USAGE ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${DB_ROLE_PREFIX}viewer;
GRANT SELECT ON ALL TABLES IN SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${DB_ROLE_PREFIX}viewer;
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${SILVER_SCHEMA} GRANT SELECT ON TABLES TO ${DB_ROLE_PREFIX}viewer;
ALTER DEFAULT PRIVILEGES FOR ROLE ${SQLMESH_DB_USER} IN SCHEMA ${GOLD_SCHEMA} GRANT SELECT ON TABLES TO ${DB_ROLE_PREFIX}viewer;

-- The crudman schema, minus the tables that hold credentials or secret tokens. The same
-- exclusions as grafana's: Django's own auth_/django_ tables and the dropzone table with
-- its upload-link tokens.
GRANT USAGE ON SCHEMA crudman TO ${DB_ROLE_PREFIX}viewer;

-- Editors and admins read everything a viewer reads, so they are made members of it
-- rather than repeating the grants. A rank is therefore cumulative by construction.
GRANT ${DB_ROLE_PREFIX}viewer TO ${DB_ROLE_PREFIX}editor;
GRANT ${DB_ROLE_PREFIX}editor TO ${DB_ROLE_PREFIX}admin;

--------------------------------------------------------------------
-- Write access for SQLMesh development.
--
-- Editors and admins develop models, which means running `sqlmesh plan`. That needs more
-- than reading: SQLMesh writes its snapshots to the state schema and materialises models
-- into the physical schemas behind the virtual layer. Those schemas are owned by the
-- sqlmesh role and are created as models appear, so the grants are applied by the event
-- trigger below rather than listed here.
--
-- This deliberately permits `sqlmesh plan prod` as well. Promoting to production is a
-- view swap in exactly the schemas a developer must be able to write to plan anything at
-- all, so PostgreSQL cannot separate the two without giving every developer their own
-- state and physical layer -- which costs a full backfill per person. The accepted
-- control is that production is normally deployed from CI on merge; see
-- sqlmesh/CLAUDE.md.
--------------------------------------------------------------------
GRANT CREATE ON DATABASE ${PG_DATABASE} TO ${DB_ROLE_PREFIX}editor;

-- SQLMesh's schemas do not exist yet at first start (the engine creates them on its first
-- plan) and new ones appear whenever a model lands in a new schema. An event trigger
-- grants the developer group access to each one as it is created, the same technique
-- gf_0005 uses to keep grafana's read access current.
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
        -- Only the schemas SQLMesh works in: its physical layer (sqlmesh__*), its state
        -- schema (sqlmesh) and the virtual layer of the medallion levels, including the
        -- staging layer and the per-environment suffixed copies a dev plan creates
        -- (silver_staging, silver__dev_marcus, ...) -- which is why these match on a
        -- prefix. starts_with rather than LIKE because a configured name may hold an
        -- underscore, which LIKE would read as a single-character wildcard.
        CONTINUE WHEN NOT starts_with(obj.object_identity, 'sqlmesh')
                  AND NOT starts_with(obj.object_identity, '${SILVER_SCHEMA}')
                  AND NOT starts_with(obj.object_identity, '${GOLD_SCHEMA}')
                  AND NOT starts_with(obj.object_identity, '${BRONZE_SCHEMA_PREFIX}');

        EXECUTE format('GRANT ALL ON SCHEMA %I TO ${DB_ROLE_PREFIX}editor', obj.object_identity);
        EXECUTE format(
            'GRANT ALL ON ALL TABLES IN SCHEMA %I TO ${DB_ROLE_PREFIX}editor', obj.object_identity
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT ALL ON TABLES TO ${DB_ROLE_PREFIX}editor',
            (SELECT nspowner::regrole FROM pg_namespace WHERE nspname = obj.object_identity),
            obj.object_identity
        );
    END LOOP;
END;
$$;

CREATE EVENT TRIGGER developer_write_on_create_schema
    ON ddl_command_end
    WHEN TAG IN ('CREATE SCHEMA')
    EXECUTE FUNCTION grant_developer_write();

-- The two schemas that already exist at this point (created by gf_0005) predate the
-- trigger, so they are granted directly.
GRANT ALL ON SCHEMA ${SILVER_SCHEMA}, ${GOLD_SCHEMA} TO ${DB_ROLE_PREFIX}editor;

-- The engine's own run has to be able to read and replace what a developer's plan
-- materialised, which it cannot do for a table another role owns. Rather than making the
-- production role a member of the developer group -- which would widen what the deployed
-- engine may do -- every developer's role is created with sqlmesh as a default member of
-- the objects it creates; see create_db_user in gf_0003.

--------------------------------------------------------------------
-- Who may provision database users.
--
-- PostgreSQL grants EXECUTE on a new function to PUBLIC by default, which for a
-- SECURITY DEFINER function that can CREATE ROLE means every role in the cluster could
-- provision itself an admin account. The default is therefore revoked and the privilege
-- given to crudman alone, which is the only thing that legitimately calls these.
--
-- They live in gf_0003 with the tenant functions, but the grant has to wait until here:
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
