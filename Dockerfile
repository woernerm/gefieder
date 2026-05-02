FROM pgduckdb/pgduckdb:17-main

COPY --chown=postgres:postgres setup_01.sh /docker-entrypoint-initdb.d/
COPY --chown=postgres:postgres setup_02.sql /docker-entrypoint-initdb.d/

