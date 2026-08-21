# Create the dedicated database users with the passwords from the mounted secrets.
# The password is passed as a psql variable and expanded via format() outside of any
# quoted string, because psql does not interpolate variables inside dollar-quoted
# (DO $$ ... $$) blocks.
#
# The role name and the secret are passed separately because they need not agree: both come
# from buildtime.env (postgresql/render.sh substituted them above), but a role may be
# renamed to avoid a collision in the cluster while the secret keeps the name of the
# component that owns the password, and vice versa.
create_user() {
  local user="$1"
  local secret="$2"
  local password
  password="$(cat "/run/secrets/${secret}")"

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

create_user '${CRUDMAN_DB_USER}' '${SECRET_CRUDMAN_PASSWORD}'
create_user '${SQLMESH_DB_USER}' '${SECRET_SQLMESH_PASSWORD}'
create_user '${GRAFANA_DB_USER}' '${SECRET_GRAFANA_PASSWORD}'

# SQLMesh creates and owns its own schemas (state schema as well as the physical and
# view schemas of its models), so it only needs the CREATE privilege on the database.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" -v user='${SQLMESH_DB_USER}' <<'SQL'
GRANT CREATE ON DATABASE :"db" TO :"user";
SQL
