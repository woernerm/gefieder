# The registry the base image is pulled from (DOCKER_IO_MIRROR in buildtime.env), so a
# company mirror can stand in.
ARG DOCKER_IO_MIRROR=docker.io

# Plain PostgreSQL, no extension beyond what ships with it: DuckDB runs in the SQLMesh
# engine's duckdb gateway instead, so a query is either PostgreSQL's SQL or DuckDB's and
# never a mix of the two. The major version is pinned because a new one cannot open the
# data directory of the last.
FROM ${DOCKER_IO_MIRROR}/library/postgres:19

# The base image reads POSTGRES_PASSWORD_FILE to load the password from the mounted podman
# secret. Which secret is SECRET_PG_SUPERUSER_PASSWORD, passed as a --build-arg, so this and
# the quadlet's Environment= line cannot drift apart.
ARG SECRET_PG_SUPERUSER_PASSWORD=postgres_password
ENV POSTGRES_PASSWORD_FILE=/run/secrets/${SECRET_PG_SUPERUSER_PASSWORD}

# The server-statistics schema, as an ENV so the .sh init scripts create the schema under
# it and the running server carries it for anyone inspecting the image.
ARG SERVER_STATS_SCHEMA=server_stats
ENV SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}

# PostgreSQL runs these when the container starts. They come from postgresql/.initdb, which
# build.sh fills by running postgresql/render.sh: it substitutes the role names from
# buildtime.env, which psql cannot interpolate inside a plpgsql function body.
COPY --chown=postgres:postgres postgresql/.initdb/ /docker-entrypoint-initdb.d/

# The base image runs the scripts above only on an empty data directory, so this entrypoint
# re-applies the structural ones on every start and then execs the base one.
COPY --chown=postgres:postgres postgresql/entrypoint.sh /gf-entrypoint.sh
ENTRYPOINT ["/gf-entrypoint.sh"]
CMD ["postgres"]
