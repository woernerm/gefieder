# The DuckDB community extensions are downloaded here rather than by the running server,
# so the target machine needs no internet access. The DuckDB CLI writes the extension and
# its .info sidecar in the versioned layout pg_duckdb expects; hand-fetching the .gz would
# leave the sidecar missing and the extension unusable.
#
# The CLI has to be the exact DuckDB version that pg_duckdb runs, because the extension
# tree is version-scoped (<version>/<platform>/) and DuckDB ignores an extension built for
# another version. That version is not hardcoded here: it is read from the base image's own
# libduckdb.so, so bumping the base image cannot leave a stale version behind. Reading it only
# needs a Python to call the exported function, and libcurl, which that library links against;
# both come with the uv base image and its curl package.
#
# The registries the base images are pulled from, so a company mirror can stand in for a
# slow-to-reach docker.io or ghcr.io (see the *_MIRROR settings in buildtime.env).
ARG DOCKER_IO_MIRROR=docker.io
ARG GHCR_IO_MIRROR=ghcr.io

# The base image is named once, in PGDUCKDB_IMAGE, because the stage that reads the DuckDB
# version and the stage that finally runs it have to be the same image for the version to
# mean anything.
ARG PGDUCKDB_IMAGE=${DOCKER_IO_MIRROR}/pgduckdb/pgduckdb:17-main

FROM ${PGDUCKDB_IMAGE} AS pgduckdb

# The same base image the crudman and sqlmesh services build on, so the build pulls one
# base image fewer. Nothing here depends on it beyond its Python: the extensions are
# downloaded prebuilt and only their files are copied into the final image.
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim AS extensions

# The extensions to ship, comma-separated. Deliberately without a default: the list lives
# in DUCKDB_EXTENSIONS in buildtime.env (which also explains the separator) and build.sh
# passes it as a --build-arg, so a copy here would be a second place to edit and would
# quietly win whenever someone builds this Dockerfile directly.
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

# INSTALL ... FROM community fetches each extension into the versioned tree under
# /opt/duckdb-extensions. Installing them one per statement makes a failing download name
# the extension that caused it instead of failing the whole batch anonymously. An unset
# DUCKDB_EXTENSIONS would otherwise install nothing and produce an image whose queries
# fail only later, at runtime, so it stops the build here instead.
RUN test -n "${DUCKDB_EXTENSIONS}" \
      || { echo "DUCKDB_EXTENSIONS is empty; build via build.sh, which reads buildtime.env." >&2; exit 1; } \
    && for ext in $(echo "${DUCKDB_EXTENSIONS}" | tr ',' ' '); do \
         echo "Installing DuckDB extension: ${ext}"; \
         duckdb -c "SET extension_directory='/opt/duckdb-extensions'; INSTALL ${ext} FROM community;" || exit 1; \
       done


# ARG is repeated because a FROM-level ARG goes out of scope at each new stage.
ARG PGDUCKDB_IMAGE
FROM ${PGDUCKDB_IMAGE}

# The base PostgreSQL image reads POSTGRES_PASSWORD_FILE to load the password from
# the mounted Podman secret file at runtime.
ENV POSTGRES_PASSWORD_FILE=/run/secrets/superuser_password

# The schema name for the server-statistics data (see buildtime.env). It is baked in as
# an ENV so the .sh init scripts can create the schema under it and so the running server
# carries it for anyone inspecting the image. build.sh passes the value as a --build-arg.
ARG SERVER_STATS_SCHEMA=server_stats
ENV SERVER_STATS_SCHEMA=${SERVER_STATS_SCHEMA}

# The pre-downloaded extensions live in the image, not in the data volume: they are part
# of the build artifact, so a fresh volume (or a restore from backup) does not need to
# re-fetch them. gf_0001 points duckdb.extension_directory here.
COPY --from=extensions --chown=postgres:postgres /opt/duckdb-extensions/ /opt/duckdb-extensions/

# Setup PostgreSQL using the /docker-entrypoint-initdb.d/ entry point. PostgreSQL will
# run the scripts when the container starts.
COPY --chown=postgres:postgres postgresql/initdb/ /docker-entrypoint-initdb.d/
