-- The name check every function below starts with.
--
-- Each takes a role name from crudman and interpolates it with format()'s %I, so a name
-- that is not a plain identifier is a caller bug rather than an injection risk. Shared so
-- the checks cannot drift apart; `label` names the offending parameter, keeping the
-- messages the callers already raise.
--
-- Called schema-qualified because most callers pin search_path to pg_catalog alone, which
-- cannot resolve a function in public.
CREATE OR REPLACE FUNCTION public.validate_identifier(name text, label text)
RETURNS void
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog
AS $$
BEGIN
    IF name IS NULL OR name = '' THEN
        RAISE EXCEPTION '% cannot be empty', label;
    END IF;

    -- 50, not PostgreSQL's 63: crudman's prefixes have to fit too.
    IF length(name) > 50 THEN
        RAISE EXCEPTION '% exceeds maximum length of 50 characters', label;
    END IF;

    IF name !~ '^[a-zA-Z0-9_]+$' THEN
        RAISE EXCEPTION '% can only contain letters, numbers, and underscores', label;
    END IF;

    IF name ~ '^[0-9]' THEN
        RAISE EXCEPTION '% cannot start with a number', label;
    END IF;
END;
$$;

-- Function a session can call to toggle duckdb.force_execution
CREATE OR REPLACE FUNCTION use_duckdb(enable boolean)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
-- Pinned search_path, so a caller cannot shadow what this definer-owned function
-- resolves.
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

--------------------------------------------------------------------
-- The roles the user-provisioning functions below must never touch.
--
-- The service roles are the ones the deployment authenticates as from podman secrets, so
-- disabling, clearing or dropping one takes a component down. Their names are valid
-- identifiers like any other, so nothing but this check stops a caller passing one.
--
-- Derived rather than listed, the superuser's name being configurable: a hardcoded
-- 'postgres' would protect a role that does not exist here. The application roles are
-- recognised by owning the database's schemas, the superuser by rolsuper.
CREATE OR REPLACE FUNCTION is_protected_role(role_name text)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = role_name
          AND (rolsuper OR rolname IN ('${CRUDMAN_DB_USER}', '${SQLMESH_DB_USER}', '${GRAFANA_DB_USER}'))
    );
$$;


-- Whether a role is one crudman provisioned for a person.
--
-- Membership of the ${ROLE_PREFIX}person marker gf_0008 creates, which create_db_user
-- grants and nothing revokes. A recorded fact rather than a name pattern: a personal role
-- shares its namespace with every other role in the cluster, so DB_USER_PREFIX is
-- readability, not a boundary. Not the rank membership, which delete_db_user strips to
-- lock someone out while a disabled account still has to be droppable.
--
-- pg_auth_members rather than pg_has_role, which is transitive and true of every
-- superuser.
CREATE OR REPLACE FUNCTION is_db_user(role_name text)
RETURNS boolean
LANGUAGE sql
STABLE
SET search_path = pg_catalog
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM pg_auth_members m
        JOIN pg_roles member ON member.oid = m.member
        JOIN pg_roles marker ON marker.oid = m.roleid
        WHERE member.rolname = role_name
          AND marker.rolname = '${ROLE_PREFIX}person'
    );
$$;


-- Per-administrator database users.
--
-- An administrator who develops SQLMesh models or runs ad-hoc SQL gets a login role of
-- their own instead of sharing the sqlmesh secret. crudman creates it (see
-- crudman/app/dbusers/), hence SECURITY DEFINER: CREATE ROLE needs the CREATEROLE crudman
-- does not have and should not be given.
--
-- The rank is not a privilege on the role but membership of one of gf_0008's group roles,
-- so what a rank means is written down in one place.
--------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_db_user(
    user_name text,
    user_password text,
    -- One of gf_0008's group roles. Passed rather than derived, so the mapping from a
    -- single sign-on rank stays in crudman, where the rank is known.
    group_role text
)
RETURNS void
LANGUAGE plpgsql
-- search_path pinned against shadowing; every identifier goes through %I/%L.
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM public.validate_identifier(user_name, 'user_name');

    -- Only the three group roles are assignable: otherwise crudman could grant itself
    -- membership of any role in the cluster, superusers included.
    IF group_role NOT IN ('${ROLE_PREFIX}viewer', '${ROLE_PREFIX}editor', '${ROLE_PREFIX}admin') THEN
        RAISE EXCEPTION 'group_role must be one of ${ROLE_PREFIX}viewer, ${ROLE_PREFIX}editor, ${ROLE_PREFIX}admin';
    END IF;

    -- The name may be free or already ours, nothing else: the branch below ALTERs an
    -- existing role, which for an unrelated role would reset its password and hand it to
    -- the person. Only the marker tells the two apart.
    IF public.is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to modify the service role %', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name)
       AND NOT public.is_db_user(user_name) THEN
        RAISE EXCEPTION 'refusing to take over %, which is not a provisioned user role', user_name;
    END IF;

    -- Optional: with an external identity provider the role carries no password of its
    -- own, so only its absence is expressed here.
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
        -- Re-provisioning resets the password and re-enables login, which is how a
        -- locked-out account comes back.
        IF user_password IS NULL THEN
            EXECUTE format('ALTER ROLE %I LOGIN', user_name);
        ELSE
            EXECUTE format(
                'ALTER ROLE %I LOGIN PASSWORD %L', user_name, user_password
            );
        END IF;
    END IF;

    -- Exactly one rank at a time, so a demotion removes rights rather than adding a
    -- second rank.
    EXECUTE format('REVOKE ${ROLE_PREFIX}viewer FROM %I', user_name);
    EXECUTE format('REVOKE ${ROLE_PREFIX}editor FROM %I', user_name);
    EXECUTE format('REVOKE ${ROLE_PREFIX}admin FROM %I', user_name);
    EXECUTE format('GRANT %I TO %I', group_role, user_name);

    -- The marker that makes this role droppable later; see is_db_user.
    EXECUTE format('GRANT ${ROLE_PREFIX}person TO %I', user_name);

    -- What this person creates must stay usable by the deployed engine, which runs as
    -- the analytics role.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON TABLES TO ${SQLMESH_DB_USER}', user_name
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I GRANT ALL ON SEQUENCES TO ${SQLMESH_DB_USER}', user_name
    );

    RAISE NOTICE 'Database user % provisioned as %', user_name, group_role;
END;
$$;


-- Take away a person's database access without destroying what they made.
--
-- NOLOGIN rather than DROP ROLE: dropping fails while the role still owns objects, and
-- forcing it through would delete models and tables that are still wanted, along with the
-- record of who made them.
--
-- public is on the search_path of this and the two functions below only so they resolve
-- is_protected_role and is_db_user. Nothing else they reference is unqualified, and the
-- provisioned roles hold no CREATE on public, so the shadowing guard still holds.
CREATE OR REPLACE FUNCTION delete_db_user(user_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public.validate_identifier(user_name, 'user_name');

    -- The service roles must not be lockable out through this.
    IF is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to modify the service role %', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        IF NOT is_db_user(user_name) THEN
            RAISE EXCEPTION 'refusing to modify %, which is not a provisioned user role', user_name;
        END IF;

        EXECUTE format('ALTER ROLE %I NOLOGIN', user_name);
        EXECUTE format('REVOKE ${ROLE_PREFIX}viewer FROM %I', user_name);
        EXECUTE format('REVOKE ${ROLE_PREFIX}editor FROM %I', user_name);
        EXECUTE format('REVOKE ${ROLE_PREFIX}admin FROM %I', user_name);
        RAISE NOTICE 'Database user % disabled', user_name;
    END IF;
END;
$$;


-- Clear a person's database password, leaving the role and its rank intact.
--
-- crudman puts the account back into "awaiting credential" and issues a fresh password at
-- the person's next sign-in. Clearing it now rather than then means a leaked credential
-- stops working immediately.
--
-- With scram-sha-256 a role holding no password cannot authenticate, so LOGIN may stay,
-- keeping the flag that distinguishes this from delete_db_user's disabled account.
CREATE OR REPLACE FUNCTION clear_db_user_password(user_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public.validate_identifier(user_name, 'user_name');

    -- Clearing a service role's password would take the deployment down.
    IF is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to modify the service role %', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        IF NOT is_db_user(user_name) THEN
            RAISE EXCEPTION 'refusing to modify %, which is not a provisioned user role', user_name;
        END IF;

        EXECUTE format('ALTER ROLE %I PASSWORD NULL', user_name);
        RAISE NOTICE 'Password cleared for database user %', user_name;
    END IF;
END;
$$;


-- Remove a person's database role entirely.
--
-- The destructive counterpart to delete_db_user, for an account created by mistake or a
-- departure leaving nothing worth keeping. The default when someone leaves is to disable,
-- so their objects keep an owner.
--
-- DROP ROLE is refused while anything still depends on the role, so its grants and owned
-- objects are cleared first. This therefore destroys the tables the person owned.
CREATE OR REPLACE FUNCTION drop_db_user(user_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM public.validate_identifier(user_name, 'user_name');

    IF is_protected_role(user_name) THEN
        RAISE EXCEPTION 'refusing to drop the service role %', user_name;
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = user_name) THEN
        -- Otherwise a caller passing the wrong name would drop an unrelated role, and
        -- whatever it owns with it.
        IF NOT is_db_user(user_name) THEN
            RAISE EXCEPTION 'refusing to drop %, which is not a provisioned user role', user_name;
        END IF;

        EXECUTE format('DROP OWNED BY %I CASCADE', user_name);
        EXECUTE format('DROP ROLE %I', user_name);
        RAISE NOTICE 'Database user % dropped', user_name;
    END IF;
END;
$$;
