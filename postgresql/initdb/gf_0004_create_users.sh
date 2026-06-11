# Create the dedicated Crudman database user with the password from the mounted secret.
# The password is passed as a psql variable and expanded via format() outside of any
# quoted string, because psql does not interpolate variables inside dollar-quoted
# (DO $$ ... $$) blocks.
crudman_password="$(cat /run/secrets/crudman_password)"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v crudman_password="$crudman_password" <<'SQL'
SELECT format('CREATE ROLE crudman LOGIN PASSWORD %L', :'crudman_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crudman')
\gexec

SELECT format('ALTER ROLE crudman WITH PASSWORD %L', :'crudman_password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'crudman')
\gexec
SQL
