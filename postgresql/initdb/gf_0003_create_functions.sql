-- Function tenants can call to toggle duckdb.force_execution
CREATE OR REPLACE FUNCTION use_duckdb(enable boolean)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
-- Pin the search_path so a caller cannot shadow objects this definer-owned function
-- resolves; standard hardening for SECURITY DEFINER functions.
SET search_path = pg_catalog
AS $$
BEGIN
    IF enable THEN
        PERFORM set_config('duckdb.force_execution', 'true', false);
    ELSE
        PERFORM set_config('duckdb.force_execution', 'false', false);
    END IF;
END;
$$;

-- Main onboarding function
CREATE OR REPLACE FUNCTION create_tenant(
    tenant_name text,
    tenant_password text,
    -- The human-readable name (e.g. "Project A") shown in the admin. It is stored as a
    -- COMMENT on the bronze schema so the catalog — the source of truth for tenants —
    -- carries it too, not just the crudman cache. Defaults to the slug so callers that do
    -- not supply one (and tenants created before this column existed) still have a name.
    tenant_display_name text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
-- SECURITY DEFINER so the unprivileged application role (crudman) can onboard tenants:
-- the function runs with its owner's rights (the bootstrap superuser), which has the
-- CREATEROLE needed for the CREATE ROLE below. The search_path is pinned to pg_catalog
-- so a caller cannot shadow the objects this definer-owned function resolves; all
-- identifiers are interpolated with format()'s %I/%L, so no unqualified user objects are
-- referenced. public is included only so the GRANT EXECUTE ON FUNCTION use_duckdb(...)
-- below can resolve that function, which lives in public.
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    schema_name text := 'bronze_' || tenant_name;
BEGIN
    --------------------------------------------------------------------
    -- Input validation
    --------------------------------------------------------------------
    -- Check tenant_name is not empty
    IF tenant_name IS NULL OR tenant_name = '' THEN
        RAISE EXCEPTION 'tenant_name cannot be empty';
    END IF;

    -- Check tenant_name length (PostgreSQL identifier limit is 63)
    IF length(tenant_name) > 50 THEN
        RAISE EXCEPTION 'tenant_name exceeds maximum length of 50 characters';
    END IF;

    -- Check tenant_name contains only valid characters (alphanumeric and underscore)
    IF tenant_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'tenant_name can only contain letters, numbers, and underscores';
    END IF;

    -- Check tenant_name doesn't start with a number (PostgreSQL identifier requirement)
    IF tenant_name ~ '^[0-9]' THEN
        RAISE EXCEPTION 'tenant_name cannot start with a number';
    END IF;

    -- Check tenant_password is not empty
    IF tenant_password IS NULL OR tenant_password = '' THEN
        RAISE EXCEPTION 'tenant_password cannot be empty';
    END IF;

    -- Check tenant_password minimum length
    IF length(tenant_password) < 8 THEN
        RAISE EXCEPTION 'tenant_password must be at least 8 characters long';
    END IF;

    --------------------------------------------------------------------
    -- Create tenant role if missing
    --------------------------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = tenant_name
    ) THEN
        EXECUTE format(
            'CREATE ROLE %I LOGIN PASSWORD %L',
            tenant_name,
            tenant_password
        );
    ELSE
        -- If role exists, update password
        EXECUTE format(
            'ALTER ROLE %I WITH PASSWORD %L',
            tenant_name,
            tenant_password
        );
    END IF;

    --------------------------------------------------------------------
    -- Ensure tenant_id is always set on login
    --------------------------------------------------------------------
    EXECUTE format(
        'ALTER ROLE %I SET app.tenant_id = %L',
        tenant_name,
        tenant_name
    );

    --------------------------------------------------------------------
    -- Allow tenant to toggle DuckDB execution mode via secure function
    --------------------------------------------------------------------
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION use_duckdb(boolean) TO %I',
        tenant_name
    );

    --------------------------------------------------------------------
    -- Create bronze schema
    --------------------------------------------------------------------
    EXECUTE format(
        'CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I',
        schema_name,
        tenant_name
    );

    --------------------------------------------------------------------
    -- Store the human-readable name on the schema so the catalog carries it. Falls back
    -- to the slug when no display name is given, so get_tenants always reads a name.
    --------------------------------------------------------------------
    EXECUTE format(
        'COMMENT ON SCHEMA %I IS %L',
        schema_name,
        coalesce(nullif(tenant_display_name, ''), tenant_name)
    );

    --------------------------------------------------------------------
    -- Grant privileges inside bronze schema
    --------------------------------------------------------------------
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', schema_name, tenant_name);
    EXECUTE format('GRANT CREATE ON SCHEMA %I TO %I', schema_name, tenant_name);

    -- Default privileges for future tables
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        schema_name,
        tenant_name
    );

    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT ALL ON SEQUENCES TO %I',
        schema_name,
        tenant_name
    );

    --------------------------------------------------------------------
    -- Allow the sqlmesh user to read and write the bronze schema and to
    -- create (and drop) its own tables, views and materialized views in
    -- it. The default privileges are set FOR ROLE tenant because the
    -- tenant creates the tables.
    --------------------------------------------------------------------
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO sqlmesh', schema_name);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO sqlmesh',
        schema_name
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sqlmesh',
        tenant_name,
        schema_name
    );

    RAISE NOTICE 'Tenant % created with schema %', tenant_name, schema_name;
END;
$$;

-- Update a tenant's human-readable name, stored as the comment on its bronze schema.
-- Used when an admin renames an existing tenant; create_tenant sets the comment on
-- onboarding, this keeps it in sync afterwards. search_path and %I/%L hardening as above.
CREATE OR REPLACE FUNCTION set_tenant_display_name(
    tenant_name text,
    tenant_display_name text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    schema_name text := 'bronze_' || tenant_name;
BEGIN
    IF tenant_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'tenant_name can only contain letters, numbers, and underscores';
    END IF;

    EXECUTE format(
        'COMMENT ON SCHEMA %I IS %L',
        schema_name,
        coalesce(nullif(tenant_display_name, ''), tenant_name)
    );
END;
$$;

-- Tenant deletion function
CREATE OR REPLACE FUNCTION delete_tenant(tenant_name text)
RETURNS void
LANGUAGE plpgsql
-- SECURITY DEFINER so crudman can offboard tenants; dropping the role needs CREATEROLE,
-- which the function's superuser owner has. search_path pinned for the same reason as
-- create_tenant above.
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    schema_bronze text := 'bronze_' || tenant_name;
BEGIN
    --------------------------------------------------------------------
    -- Input validation
    --------------------------------------------------------------------
    -- Check tenant_name is not empty
    IF tenant_name IS NULL OR tenant_name = '' THEN
        RAISE EXCEPTION 'tenant_name cannot be empty';
    END IF;

    -- Check tenant_name contains only valid characters (alphanumeric and underscore)
    IF tenant_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'tenant_name can only contain letters, numbers, and underscores';
    END IF;

    --------------------------------------------------------------------
    -- Remove everything the tenant role owns or was granted, then drop it.
    --
    -- DROP ROLE refuses to run while any object still depends on the role:
    -- create_tenant grants the role EXECUTE on use_duckdb() and several
    -- privileges inside its bronze schema, and sqlmesh may have created tables
    -- in that schema too. DROP OWNED BY clears all of those grants and drops the
    -- objects the role owns — including the bronze schema it authorises — so the
    -- DROP ROLE below can succeed. Without it the role drop aborts and, because
    -- the function is atomic, the schema drop is rolled back as well, leaving the
    -- tenant only half-deleted. Skipped when the role is already gone, since
    -- DROP OWNED BY errors on an unknown role.
    --------------------------------------------------------------------
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = tenant_name) THEN
        EXECUTE format('DROP OWNED BY %I CASCADE', tenant_name);
    END IF;

    -- Safety net for the case where the role was already removed but its schema
    -- lingers; DROP OWNED BY above already drops the schema in the normal case.
    EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', schema_bronze);

    RAISE NOTICE 'Deleted schema % for tenant %', schema_bronze, tenant_name;

    --------------------------------------------------------------------
    -- Delete tenant role
    --------------------------------------------------------------------
    EXECUTE format(
        'DROP ROLE IF EXISTS %I',
        tenant_name
    );

    RAISE NOTICE 'Tenant % and all associated data deleted', tenant_name;
END;
$$;

-- Set resource limits for a tenant
CREATE OR REPLACE FUNCTION set_tenant_limits(
    tenant_name text,
    connection_limit int DEFAULT 5,
    statement_timeout text DEFAULT '5min',
    work_mem text DEFAULT '256MB',
    temp_file_limit text DEFAULT '1GB'
)
RETURNS void
LANGUAGE plpgsql
-- SECURITY DEFINER so crudman can apply a tenant's limits; ALTER ROLE needs CREATEROLE,
-- which the function's superuser owner has. search_path pinned as above.
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    --------------------------------------------------------------------
    -- Input validation
    --------------------------------------------------------------------
    -- Check tenant_name is not empty and valid
    IF tenant_name IS NULL OR tenant_name = '' THEN
        RAISE EXCEPTION 'tenant_name cannot be empty';
    END IF;

    IF tenant_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'tenant_name can only contain letters, numbers, and underscores';
    END IF;

    -- Check role exists
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = tenant_name) THEN
        RAISE EXCEPTION 'Tenant role % does not exist', tenant_name;
    END IF;

    -- "No limit" sentinels, mirroring how the values are stored and displayed elsewhere:
    -- -1 for the connection count and '0' for the size/time limits. A sentinel means the
    -- per-tenant override is removed with RESET, so the tenant falls back to the server
    -- default (i.e. no special cap); only real values are format-validated.

    -- Validate connection_limit (-1 means unlimited)
    IF connection_limit < -1 THEN
        RAISE EXCEPTION 'connection_limit must be >= -1 (unlimited)';
    END IF;

    -- Validate timeout and memory values, skipping the '0' = unlimited sentinel.
    IF statement_timeout <> '0'
       AND statement_timeout !~ '^\d+[smh]$' AND statement_timeout !~ '^\d+$' THEN
        RAISE EXCEPTION 'statement_timeout must be in format like 5min, 10s, 1h';
    END IF;

    IF work_mem <> '0' AND work_mem !~ '^\d+[kMG]B?$' THEN
        RAISE EXCEPTION 'work_mem must be in format like 256MB, 1GB';
    END IF;

    IF temp_file_limit <> '0' AND temp_file_limit !~ '^\d+[kMG]B?$' THEN
        RAISE EXCEPTION 'temp_file_limit must be in format like 1GB';
    END IF;

    --------------------------------------------------------------------
    -- Apply connection limit (-1 = unlimited is a valid CONNECTION LIMIT value)
    --------------------------------------------------------------------
    EXECUTE format(
        'ALTER ROLE %I CONNECTION LIMIT %s',
        tenant_name,
        connection_limit
    );

    --------------------------------------------------------------------
    -- Apply the resource limits, or RESET them to the server default when the '0'
    -- unlimited sentinel is given (PostgreSQL has no literal "unlimited" for these).
    --------------------------------------------------------------------
    IF statement_timeout = '0' THEN
        EXECUTE format('ALTER ROLE %I RESET statement_timeout', tenant_name);
    ELSE
        EXECUTE format(
            'ALTER ROLE %I SET statement_timeout = %L', tenant_name, statement_timeout
        );
    END IF;

    IF work_mem = '0' THEN
        EXECUTE format('ALTER ROLE %I RESET work_mem', tenant_name);
    ELSE
        EXECUTE format('ALTER ROLE %I SET work_mem = %L', tenant_name, work_mem);
    END IF;

    IF temp_file_limit = '0' THEN
        EXECUTE format('ALTER ROLE %I RESET temp_file_limit', tenant_name);
    ELSE
        EXECUTE format(
            'ALTER ROLE %I SET temp_file_limit = %L', tenant_name, temp_file_limit
        );
    END IF;

    RAISE NOTICE 'Resource limits set for tenant %: connections=%, timeout=%, work_mem=%, temp_file_limit=%',
        tenant_name,
        CASE WHEN connection_limit = -1 THEN 'unlimited' ELSE connection_limit::text END,
        statement_timeout,
        work_mem,
        temp_file_limit;
END;
$$;

--------------------------------------------------------------------
-- The roles the user-provisioning functions below must never touch.
--
-- The service roles are the ones the deployment itself authenticates as, from podman
-- secrets: disabling, clearing or dropping one takes a component down. Their names are
-- valid identifiers like any other, so nothing but this check stops a caller passing one.
--
-- Derived rather than listed, because the superuser's name is configurable (SUPERUSER_NAME
-- in buildtime.env, "admin" by default) -- a hardcoded 'postgres' would protect a role
-- that does not exist here and leave the real superuser exposed. The three application
-- roles come from gf_0004 and are recognised by owning the database's schemas; the
-- superuser is whoever holds rolsuper.
CREATE OR REPLACE FUNCTION is_protected_role(role_name text)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = role_name
          AND (rolsuper OR rolname IN ('crudman', 'sqlmesh', 'grafana'))
    );
$$;


-- Per-administrator database users.
--
-- An administrator who develops SQLMesh models or runs ad-hoc SQL gets a login role of
-- their own instead of sharing the sqlmesh secret. crudman creates it when the person is
-- provisioned in the admin (see crudman/app/dbusers/), which is why these functions are
-- SECURITY DEFINER: CREATE ROLE needs CREATEROLE, which the unprivileged crudman role
-- does not have and should not be given -- the same reasoning as create_tenant above.
--
-- The rank is not a privilege on the role itself but membership of one of the gf_* group
-- roles from gf_0008, so what a rank means is written down in one place.
--------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_db_user(
    user_name text,
    user_password text,
    -- One of the group roles in gf_0008. Passed rather than derived so the mapping from
    -- a single sign-on rank to a database rank stays in crudman, where the rank is known.
    group_role text
)
RETURNS void
LANGUAGE plpgsql
-- search_path pinned to pg_catalog so a caller cannot shadow what this definer-owned
-- function resolves; every identifier is interpolated with format()'s %I/%L.
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF user_name IS NULL OR user_name = '' THEN
        RAISE EXCEPTION 'user_name cannot be empty';
    END IF;

    IF length(user_name) > 50 THEN
        RAISE EXCEPTION 'user_name exceeds maximum length of 50 characters';
    END IF;

    IF user_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'user_name can only contain letters, numbers, and underscores';
    END IF;

    IF user_name ~ '^[0-9]' THEN
        RAISE EXCEPTION 'user_name cannot start with a number';
    END IF;

    -- Only the three group roles are assignable. Without this check the function would be
    -- a way for crudman to grant itself membership of any role in the cluster, superusers
    -- included, which is exactly what SECURITY DEFINER makes dangerous.
    IF group_role NOT IN ('gf_viewer', 'gf_editor', 'gf_admin') THEN
        RAISE EXCEPTION 'group_role must be one of gf_viewer, gf_editor, gf_admin';
    END IF;

    -- A password is optional: with an external identity provider the role authenticates
    -- against it and carries no password of its own. Only its absence is expressed here,
    -- so switching authentication method later does not change this function.
    IF user_password IS NOT NULL AND length(user_password) < 12 THEN
        RAISE EXCEPTION 'user_password must be at least 12 characters long';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        IF user_password IS NULL THEN
            EXECUTE format('CREATE ROLE %I LOGIN', user_name);
        ELSE
            EXECUTE format(
                'CREATE ROLE %I LOGIN PASSWORD %L', user_name, user_password
            );
        END IF;
    ELSE
        -- Re-provisioning an existing person resets the password and re-enables login,
        -- which is how a locked-out account (see delete_db_user) is brought back.
        IF user_password IS NULL THEN
            EXECUTE format('ALTER ROLE %I LOGIN', user_name);
        ELSE
            EXECUTE format(
                'ALTER ROLE %I LOGIN PASSWORD %L', user_name, user_password
            );
        END IF;
    END IF;

    -- Exactly one rank at a time: the old membership is dropped before the new one is
    -- granted, so a demotion actually removes rights instead of adding a second rank.
    EXECUTE format('REVOKE gf_viewer FROM %I', user_name);
    EXECUTE format('REVOKE gf_editor FROM %I', user_name);
    EXECUTE format('REVOKE gf_admin FROM %I', user_name);
    EXECUTE format('GRANT %I TO %I', group_role, user_name);

    -- What this person creates while developing must stay usable by the deployed engine,
    -- which runs as sqlmesh and cannot otherwise touch a table another role owns.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON TABLES TO sqlmesh', user_name
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON SEQUENCES TO sqlmesh', user_name
    );

    RAISE NOTICE 'Database user % provisioned as %', user_name, group_role;
END;
$$;


-- Take away a person's database access without destroying what they made.
--
-- NOLOGIN rather than DROP ROLE on purpose: dropping fails while the role still owns
-- objects, and forcing it through (DROP OWNED BY) would delete models and tables that
-- are still wanted, along with the record of who made them. Someone who has left keeps
-- their name on their objects; they simply cannot connect any more.
--
-- public is on the search_path of this and the two functions below only so they can
-- resolve is_protected_role, which lives there; the same reason create_tenant carries it.
-- Nothing else they reference is unqualified, and CREATE on public is not granted to the
-- provisioned roles, so this does not reopen the shadowing the pinned path prevents.
CREATE OR REPLACE FUNCTION delete_db_user(user_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF user_name IS NULL OR user_name = '' THEN
        RAISE EXCEPTION 'user_name cannot be empty';
    END IF;

    IF user_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'user_name can only contain letters, numbers, and underscores';
    END IF;

    -- Guard against this function being used to lock out the service roles.
    IF is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to modify the service role %', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        EXECUTE format('ALTER ROLE %I NOLOGIN', user_name);
        EXECUTE format('REVOKE gf_viewer FROM %I', user_name);
        EXECUTE format('REVOKE gf_editor FROM %I', user_name);
        EXECUTE format('REVOKE gf_admin FROM %I', user_name);
        RAISE NOTICE 'Database user % disabled', user_name;
    END IF;
END;
$$;


-- Clear a person's database password, leaving the role and its rank intact.
--
-- How a forgotten or possibly leaked password is handled: crudman puts the account back
-- into the "awaiting credential" state and a fresh password is issued on the person's next
-- sign-in. Clearing it here rather than at that sign-in means a credential that may have
-- leaked stops working immediately.
--
-- With scram-sha-256 a role holding no password cannot authenticate, so LOGIN may stay:
-- the account is unusable until the new password is set, without losing the flag that
-- distinguishes it from one disabled by delete_db_user.
CREATE OR REPLACE FUNCTION clear_db_user_password(user_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF user_name IS NULL OR user_name = '' THEN
        RAISE EXCEPTION 'user_name cannot be empty';
    END IF;

    IF user_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'user_name can only contain letters, numbers, and underscores';
    END IF;

    -- The service roles authenticate from podman secrets; clearing one would take the
    -- deployment down, so they are refused here as they are in delete_db_user.
    IF is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to modify the service role %', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        EXECUTE format('ALTER ROLE %I PASSWORD NULL', user_name);
        RAISE NOTICE 'Password cleared for database user %', user_name;
    END IF;
END;
$$;


-- Remove a person's database role entirely.
--
-- The counterpart to delete_db_user, which only locks the account: this one is for an
-- account created by mistake, or a departure where the person left nothing behind worth
-- keeping. It is the destructive option and is offered separately for that reason -- the
-- default when someone leaves is to disable, so their models and tables keep an owner and
-- the audit trail survives.
--
-- DROP ROLE is refused while anything still depends on the role, so its grants and owned
-- objects are cleared first, exactly as delete_tenant does. That means this DOES destroy
-- tables the person owned: the caller is expected to have decided that already.
CREATE OR REPLACE FUNCTION drop_db_user(user_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF user_name IS NULL OR user_name = '' THEN
        RAISE EXCEPTION 'user_name cannot be empty';
    END IF;

    IF user_name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION 'user_name can only contain letters, numbers, and underscores';
    END IF;

    IF is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to drop the service role %', user_name;
    END IF;

    -- Refuse anything that is not a provisioned personal account. Without this the
    -- function would drop a tenant role -- and its bronze schema with it -- for a caller
    -- who passed the wrong name.
    IF user_name !~ '^gf_u_' THEN
        RAISE EXCEPTION 'refusing to drop %, which is not a provisioned user role', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        EXECUTE format('DROP OWNED BY %I CASCADE', user_name);
        EXECUTE format('DROP ROLE %I', user_name);
        RAISE NOTICE 'Database user % dropped', user_name;
    END IF;
END;
$$;
