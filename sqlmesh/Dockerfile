# The registry the base image is pulled from, so a company mirror can stand in for a
# slow-to-reach ghcr.io (see GHCR_IO_MIRROR in buildtime.env).
ARG GHCR_IO_MIRROR=ghcr.io
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim

# Prevent Python from writing .pyc files. Since in a container, the Python process is
# invoked only once, there is no need to write bytecode files.
ENV PYTHONDONTWRITEBYTECODE=1

# Forces Python to write stdout/stderr immediately.
# This is useful for Docker logs and makes errors appear in real time.
ENV PYTHONUNBUFFERED=1

WORKDIR /sqlmesh/app

# Company proxy for the dependency install below, passed as --build-arg by CI so the
# build works from behind a corporate proxy. Scoped to this stage and not persisted in
# the image. Empty by default, i.e. a direct connection.
ARG http_proxy
ARG https_proxy
ARG no_proxy

# Extra Python package index (a company-internal PyPI mirror). uv reads UV_EXTRA_INDEX_URL
# and tries it in addition to PyPI, like pip's --extra-index-url. Scoped to this stage and
# not persisted in the image; empty by default, i.e. PyPI only.
ARG PYTHON_INDEX

# Copy dependency metadata first so Docker can cache dependency installation.
COPY sqlmesh/pyproject.toml /sqlmesh/pyproject.toml

# Install the project dependencies defined in pyproject.toml.
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /sqlmesh --no-dev

# The DuckDB extensions to ship, comma-separated -- the same list the database image gets,
# and for the same reason. config.py defines a DuckDB gateway (models can ask for it with
# `gateway duckdb`), and a query that runs there has to reach the same extensions a tenant
# reaches through use_duckdb(). Deliberately without a default: the list lives in
# DUCKDB_EXTENSIONS in buildtime.env and every builder passes it as a --build-arg, so a
# copy here would be a second place to edit and would quietly win.
ARG DUCKDB_EXTENSIONS

# Kept in the image so config.py can read the list back rather than repeat it: the gateway
# loads exactly what was installed below, and neither list can drift from the other.
ENV DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}

# Pinned so the directory the extensions are installed into below is the one DuckDB looks
# in at runtime. It cannot be moved with `extension_directory`: SQLMesh loads a gateway's
# extensions before it applies any DuckDB setting, so only DuckDB's own default is early
# enough. The database image can point at /opt/duckdb-extensions precisely because nothing
# there loads them before postgresql.conf is read.
ENV HOME=/root

# Installed with the interpreter that will later load them, so the extension files match
# its DuckDB version exactly -- the database image has to work the version out of
# libduckdb.so instead. One statement per extension, so a failing download names the
# extension that caused it. An unset list would produce an image whose queries fail only
# at runtime, so it stops the build here instead.
RUN test -n "${DUCKDB_EXTENSIONS}" \
      || { echo "DUCKDB_EXTENSIONS is empty; build via build.sh, which reads buildtime.env." >&2; exit 1; } \
    && for ext in $(echo "${DUCKDB_EXTENSIONS}" | tr ',' ' '); do \
         echo "Installing DuckDB extension: ${ext}"; \
         uv run --project /sqlmesh python -c \
           "import duckdb; duckdb.connect().execute('INSTALL ${ext} FROM community')" || exit 1; \
       done

# postgres is not part of that list and is not the operator's to choose: it is how the
# gateway attaches PostgreSQL, so without it there is no gateway at all. A core extension
# rather than a community one, hence the missing FROM.
RUN uv run --project /sqlmesh python -c \
      "import duckdb; duckdb.connect().execute('INSTALL postgres')"

# Copy the SQLMesh project into the image. In the container the engine logs to stdout and
# writes its files under /tmp (see entrypoint.sh), and the password comes from the mounted
# podman secret, so a developer's sqlmesh/logs/ and sqlmesh/.env have no business here.
# The .dockerignore keeps them out of the build context; the removal below is a second
# line of defence for the password, which must never reach a distributed image.
COPY sqlmesh/ /sqlmesh/app/
RUN rm -rf /sqlmesh/app/logs /sqlmesh/app/.env

# Copy the startup script that applies the plan and runs the scheduling loop. It is
# committed executable, so a plain COPY (no BuildKit --chmod) preserves the mode.
COPY sqlmesh/entrypoint.sh /sqlmesh/entrypoint.sh

CMD ["/sqlmesh/entrypoint.sh"]
