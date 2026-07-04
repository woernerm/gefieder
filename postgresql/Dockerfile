FROM docker.io/pgduckdb/pgduckdb:17-main

# The base PostgreSQL image reads POSTGRES_PASSWORD_FILE to load the password from
# the mounted Podman secret file at runtime.
ENV POSTGRES_PASSWORD_FILE=/run/secrets/superuser_password

# The schema name for the server-statistics data (see buildtime.env). It is baked in as
# an ENV so the .sh init scripts can create the schema under it and so the running server
# carries it for anyone inspecting the image. build.sh passes the value as a --build-arg.
ARG SERVER_STATS_SCHEMA=server_stats
ENV SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}

# Setup PostgreSQL using the /docker-entrypoint-initdb.d/ entry point. PostgreSQL will
# run the scripts when the container starts.
COPY --chown=postgres:postgres postgresql/initdb/ /docker-entrypoint-initdb.d/