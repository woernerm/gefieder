# The DuckDB community extensions are downloaded here rather than by the running server, so
# the target machine needs no internet access. The DuckDB CLI writes the extension and its
# .info sidecar in the versioned layout pg_duckdb expects, which a hand-fetched .gz lacks.
#
# The CLI has to be the exact DuckDB version pg_duckdb runs, the extension tree being
# version-scoped. That version is read from the base image's own libduckdb.so rather than
# hardcoded, so bumping the base image cannot leave a stale one behind.
#
# The registries the base images are pulled from, so a company mirror can stand in (see the
# *_MIRROR settings in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
ARG GHCR_IO_MIRROR=ghcr.io

# Named once: the stage that reads the DuckDB version and the one that runs it have to be
# the same image for that version to mean anything.
ARG PGDUCKDB_IMAGE=${DOCKER_IO_MIRROR}/pgduckdb/pgduckdb:17-main

FROM ${PGDUCKDB_IMAGE} AS pgduckdb

# The same base image crudman and sqlmesh build on, so the build pulls one fewer. Nothing
# here needs more than its Python.
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim AS extensions

# The extensions to ship, comma-separated. Without a default, the list living in
# DUCKDB_EXTENSIONS in buildtime.env: a copy here would be a second place to edit.
ARG DUCKDB_EXTENSIONS

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pgduckdb /usr/lib/postgresql/17/lib/libduckdb.so /tmp/libduckdb.so

RUN duckdb_version="$(python3 -c "import ctypes; lib = ctypes.CDLL('/tmp/libduckdb.so'); lib.duckdb_library_version.restype = ctypes.c_char_p; print(lib.duckdb_library_version().decode())")" \
    && echo "pg_duckdb runs DuckDB ${duckdb_version}; fetching the matching CLI" \
    && curl -fsSL -o /tmp/duckdb.zip \
         "https://github.com/duckdb/duckdb/releases/download/${duckdb_version}/duckdb_cli-linux-amd64.zip" \
    && unzip -q /tmp/duckdb.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/duckdb \
    && rm /tmp/duckdb.zip /tmp/libduckdb.so

# Into the versioned tree under /opt/duckdb-extensions, one statement per extension so a
# failing download names it. An unset DUCKDB_EXTENSIONS would install nothing and produce
# an image whose queries fail only at runtime, so it stops the build here.
RUN test -n "${DUCKDB_EXTENSIONS}" \
      || { echo "DUCKDB_EXTENSIONS is empty; build via build.sh, which reads buildtime.env." >&2; exit 1; } \
    && for ext in $(echo "${DUCKDB_EXTENSIONS}" | tr ',' ' '); do \
         echo "Installing DuckDB extension: ${ext}"; \
         duckdb -c "SET extension_directory='/opt/duckdb-extensions'; INSTALL ${ext} FROM community;" || exit 1; \
       done


# A FROM-level ARG goes out of scope at each new stage.
ARG PGDUCKDB_IMAGE
FROM ${PGDUCKDB_IMAGE}

# The base image reads POSTGRES_PASSWORD_FILE to load the password from the mounted podman
# secret. Which secret is SECRET_SUPERUSER_PASSWORD, passed as a --build-arg, so this and
# the quadlet's Environment= line cannot drift apart.
ARG SECRET_SUPERUSER_PASSWORD=superuser_password
ENV POSTGRES_PASSWORD_FILE=/run/secrets/${SECRET_SUPERUSER_PASSWORD}

# The server-statistics schema, as an ENV so the .sh init scripts create the schema under
# it and the running server carries it for anyone inspecting the image.
ARG SERVER_STATS_SCHEMA=server_stats
ENV SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}

# In the image rather than the data volume, so a fresh volume needs no re-fetch. gf_0001
# points duckdb.extension_directory here.
COPY --from=extensions --chown=postgres:postgres /opt/duckdb-extensions/ /opt/duckdb-extensions/

# PostgreSQL runs these when the container starts. They come from postgresql/.initdb, which
# build.sh fills by running postgresql/render.sh: it substitutes the role names from
# buildtime.env, which psql cannot interpolate inside a plpgsql function body.
COPY --chown=postgres:postgres postgresql/.initdb/ /docker-entrypoint-initdb.d/

# The base image runs the scripts above only on an empty data directory, so this entrypoint
# re-applies the structural ones on every start and then execs the base one.
COPY --chown=postgres:postgres postgresql/entrypoint.sh /gf-entrypoint.sh
ENTRYPOINT ["/gf-entrypoint.sh"]
CMD ["postgres"]
