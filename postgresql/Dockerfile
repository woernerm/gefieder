FROM pgduckdb/pgduckdb:17-main

# The base PostgreSQL image reads POSTGRES_PASSWORD_FILE to load the password from
# the mounted Podman secret file at runtime.
ENV POSTGRES_PASSWORD_FILE=/run/secrets/superuser_password

# Setup PostgreSQL using the /docker-entrypoint-initdb.d/ entry point. PostgreSQL will
# run the scripts when the container starts.
COPY --chown=postgres:postgres postgresql/initdb/ /docker-entrypoint-initdb.d/