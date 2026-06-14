# Create the dedicated database users with the passwords from the mounted secrets.
# The password is passed as a psql variable and expanded via format() outside of any
# quoted string, because psql does not interpolate variables inside dollar-quoted
# (DO $$ ... $$) blocks.
create_user() {
  local user="$1"
  local password
  password="$(cat "/run/secrets/${user}_password")"

  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v user="$user" -v password="$password" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'user', :'password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user')
\gexec

SELECT format('ALTER ROLE %I WITH PASSWORD %L', :'user', :'password')
WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'user')
\gexec
SQL
}

create_user crudman
create_user sqlmesh
create_user grafana

# SQLMesh creates and owns its own schemas (state schema as well as the physical and
# view schemas of its models), so it only needs the CREATE privilege on the database.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" <<'SQL'
GRANT CREATE ON DATABASE :"db" TO sqlmesh;
SQL
