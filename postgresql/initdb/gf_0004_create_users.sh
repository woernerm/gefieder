# The dedicated database users, with the passwords from the mounted secrets. The password
# is a psql variable expanded through format() outside any quoted string, psql not
# interpolating inside a dollar-quoted DO block.
#
# The role name and the secret are passed separately because they need not agree: a role
# may be renamed to dodge a collision while the secret keeps the component's name.
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

# SQLMesh creates and owns its own schemas, so it needs only CREATE on the database.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" -v user='${SQLMESH_DB_USER}' <<'SQL'
GRANT CREATE ON DATABASE :"db" TO :"user";
SQL
