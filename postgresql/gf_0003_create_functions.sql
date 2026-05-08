-- Function tenants can call to toggle duckdb.force_execution
CREATE OR REPLACE FUNCTION use_duckdb(enable boolean)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
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
CREATE OR REPLACE FUNCTION create_tenant(tenant_name text, tenant_password text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    schema_name text := tenant_name || '_bronze';
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

    RAISE NOTICE 'Tenant % created with schema %', tenant_name, schema_name;
END;
$$;

-- Tenant deletion function
CREATE OR REPLACE FUNCTION delete_tenant(tenant_name text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    schema_bronze text := tenant_name || '_bronze';
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
    -- Delete tenant's bronze schema and all its contents
    --------------------------------------------------------------------
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