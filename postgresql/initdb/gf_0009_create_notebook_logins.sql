-- The sibling login role a notebook server connects as.
--
-- The notebook runs on this machine and its credential lives in the environment of a
-- process, so it is not the password the person carries to a laptop: that one stays theirs
-- and is never seen here. This one is rotated at every server start, which makes a leak
-- expire on its own.
--
-- What makes it a sibling rather than a second account is that it is a *member* of the
-- person's own role and does nothing else. The session assumes that role -- SQLMesh's
-- "role" connection setting and the DuckDB gateway's attach string both issue SET ROLE, see
-- sqlmesh/config.py -- so current_user is the person: every table a plan creates is owned by
-- them, exactly as if they had connected from a laptop, and nothing has to be granted
-- between the two. The rank follows automatically, hanging off the person's role, so a
-- demotion applied there reaches the notebook without a second write.
--
-- The role could have defaulted into it at login (ALTER ROLE ... SET role), which would need
-- no client setting at all. PostgreSQL refuses: "cannot set parameter role within
-- security-definer function", and this function has to be one to create a role at all.
--
-- <person>_nb rather than a name of its own, so a glance at pg_stat_activity says both who
-- is connected and how.


-- The suffix on a notebook role, in one place; crudman/app/jupyterhub/utils.py spells the
-- same word.
CREATE OR REPLACE FUNCTION notebook_role_name(person_name text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    -- Left-truncated, PostgreSQL capping an identifier at 63 characters and the suffix
    -- being what makes this name distinct.
    SELECT left(person_name, 60) || '_nb';
$$;


-- Provision the notebook login of a person who already has a database account.
--
-- Called by crudman before it spawns a notebook server. SECURITY DEFINER for the CREATE
-- ROLE, like create_db_user, and equally bounded: the person's role must exist and carry
-- the marker, so this cannot mint a login for anything else.
CREATE OR REPLACE FUNCTION create_notebook_login(person_name text, user_password text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    notebook_name text;
BEGIN
    PERFORM public.validate_identifier(person_name, 'person_name');

    -- The person's own role is the only thing this may attach to. A service role has no
    -- notebook: the deployed engine is not a person, and a login defaulting into it would
    -- hand production's rights to whoever spawned the server.
    IF is_protected_role(person_name) OR NOT is_db_user(person_name) THEN
        RAISE EXCEPTION '% is not a provisioned user role', person_name;
    END IF;

    IF user_password IS NULL OR length(user_password) < 12 THEN
        RAISE EXCEPTION 'user_password must be at least 12 characters long';
    END IF;

    notebook_name := notebook_role_name(person_name);

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = notebook_name) THEN
        EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', notebook_name, user_password);
    ELSE
        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', notebook_name, user_password);
    END IF;

    -- Membership is the whole of it: the client assumes the role per session.
    EXECUTE format('GRANT %I TO %I', person_name, notebook_name);

    -- The marker, so delete_db_user and drop_db_user recognise this role as one of ours
    -- when the person is offboarded.
    EXECUTE format('GRANT ${ROLE_PREFIX}person TO %I', notebook_name);

    RETURN notebook_name;
END;
$$;


-- Remove the notebook login of a person, wherever their account ends.
--
-- Disabling the person and leaving the sibling alive would leave a working credential
-- behind, so this is called from delete_db_user and drop_db_user rather than being a step
-- a caller has to remember. Dropped rather than disabled: the notebook role owns nothing.
-- Everything a notebook creates is owned by the person, which is the point of the SET above.
CREATE OR REPLACE FUNCTION drop_notebook_login(person_name text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    notebook_name text := notebook_role_name(person_name);
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = notebook_name) THEN
        EXECUTE format('DROP OWNED BY %I CASCADE', notebook_name);
        EXECUTE format('DROP ROLE %I', notebook_name);
        RAISE NOTICE 'Notebook login % dropped', notebook_name;
    END IF;
END;
$$;


REVOKE ALL ON FUNCTION create_notebook_login(text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION drop_notebook_login(text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION create_notebook_login(text, text) TO ${CRUDMAN_DB_USER};
