# The preview database: where a version under review is read from, under the schema names
# production uses.
#
# It holds no data. Every schema in it is foreign tables pointing back at this database,
# so a preview costs one catalog entry per table and nothing else. What makes it a preview
# is which schema each name points at: SQLMesh plans a reviewed commit into its own
# environment, whose schemas are named <schema>__<environment>, and the import below
# publishes those under the bare name -- so a dashboard reading gold.issue_metrics gets
# the reviewed models without being written any differently. A schema the reviewed commit
# does not touch has no environment copy and points at production, which is what keeps a
# preview free.
#
# Grafana reaches it as a second data source; nothing else connects here.
set -e

# Lowercase, like every other shell variable in these scripts: render.sh substitutes the
# capitalised tokens it is given, and an unlisted one of those renders as empty text.
database="$POSTGRES_DB"
preview_env=preview
preview_db="${database}_${preview_env}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" \
  -v db="$preview_db" <<'SQL'
SELECT format('CREATE DATABASE %I', :'db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db')
\gexec
SQL

# The remote end is reached as the read-only Grafana role, so nothing a preview can do
# reaches production's data. One mapping for everybody: the preview database has no
# accounts of its own and every reader of it is a Grafana query.
grafana_password="$(cat "/run/secrets/${SECRET_GRAFANA_PASSWORD}")"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$preview_db" \
  -v source="$database" -v db="$preview_db" -v password="$grafana_password" <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgres_fdw;

SELECT format(
    'CREATE SERVER production FOREIGN DATA WRAPPER postgres_fdw '
    'OPTIONS (host %L, port %L, dbname %L)',
    'localhost', current_setting('port'), :'source'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_foreign_server WHERE srvname = 'production')
\gexec

-- password_required=false because the loopback is trusted in pg_hba, so the password
-- below is never asked for -- and postgres_fdw refuses a connection a non-superuser makes
-- without one being used. The password stays, so an installation that tightens pg_hba
-- keeps working. What bounds this is the remote role: read-only, and the same data both
-- roles that may connect here already read in production.
SELECT format(
    'CREATE USER MAPPING FOR PUBLIC SERVER production '
    'OPTIONS (user %L, password %L, password_required ''false'')',
    '${GRAFANA_DB_USER}', :'password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_user_mappings WHERE srvname = 'production')
\gexec

GRANT USAGE ON FOREIGN SERVER production TO ${GRAFANA_DB_USER}, ${SQLMESH_DB_USER};

SELECT format('GRANT CONNECT ON DATABASE %I TO ${GRAFANA_DB_USER}, ${SQLMESH_DB_USER}', :'db')
\gexec

-- Which schemas production has. A foreign table over its catalog rather than a list kept
-- here: a project adds a bronze schema by deploying a model that names one, and nothing
-- tells this database when that happens.
CREATE FOREIGN TABLE IF NOT EXISTS public.production_schema (nspname name)
    SERVER production
    OPTIONS (schema_name 'pg_catalog', table_name 'pg_namespace');


-- Point every schema name at the environment's copy where it has one, and at production
-- where it does not. Called after a plan, the environment's schemas being what it creates.
--
-- Definer's rights: it creates schemas and foreign tables, which the engine's role may not
-- do here, and the engine is the one that knows a plan has finished.
CREATE OR REPLACE FUNCTION public.refresh_preview(environment text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    target text;
    source text;
    suffix text := '__' || environment;
BEGIN
    FOR target, source IN
        -- Both directions at once: a schema production has, and a schema only the
        -- environment has because the reviewed commit is what adds it.
        SELECT base, COALESCE(copy.nspname, base)::text
        FROM (
            -- right(), not LIKE: the separator is underscores, which LIKE reads as
            -- single-character wildcards.
            SELECT DISTINCT
                   CASE WHEN right(nspname::text, length(suffix)) = suffix
                        THEN left(nspname::text, -length(suffix))
                        ELSE nspname::text
                   END AS base
            FROM public.production_schema
            -- Everything SQLMesh keeps for itself, and the catalogs. "public" as well:
            -- it is where this function lives, and importing over it would delete it.
            WHERE nspname NOT LIKE 'pg\_%'
              AND nspname NOT LIKE 'sqlmesh%'
              AND nspname NOT IN ('information_schema', 'public')
        ) production
        LEFT JOIN public.production_schema copy ON copy.nspname = base || suffix
    LOOP
        EXECUTE format('DROP SCHEMA IF EXISTS %I CASCADE', target);
        EXECUTE format('CREATE SCHEMA %I', target);
        EXECUTE format(
            'IMPORT FOREIGN SCHEMA %I FROM SERVER production INTO %I', source, target
        );
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO ${GRAFANA_DB_USER}', target);
        EXECUTE format(
            'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO ${GRAFANA_DB_USER}', target
        );
    END LOOP;
END;
$$;

-- The engine calls it, and nobody else has a reason to.
REVOKE EXECUTE ON FUNCTION public.refresh_preview(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.refresh_preview(text) TO ${SQLMESH_DB_USER};
SQL
