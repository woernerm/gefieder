DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crudman') THEN
        EXECUTE 'CREATE ROLE crudman LOGIN PASSWORD ' || quote_literal(:'crudman_password');
    ELSE
        EXECUTE 'ALTER ROLE crudman WITH PASSWORD ' || quote_literal(:'crudman_password');
    END IF;
END
$$;
