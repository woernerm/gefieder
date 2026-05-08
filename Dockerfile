FROM pgduckdb/pgduckdb:17-main

# Setup PostgreSQL using the /docker-entrypoint-initdb.d/ entry point. PostgreSQL will 
# run the scripts when the container starts.
COPY --chown=postgres:postgres postgresql/ /docker-entrypoint-initdb.d/

