# The registry the base image is pulled from (GHCR_IO_MIRROR in buildtime.env).
ARG GHCR_IO_MIRROR=ghcr.io
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim

# The Python process is invoked once, so bytecode files earn nothing.
ENV PYTHONDONTWRITEBYTECODE=1

# Write stdout/stderr immediately, so errors appear in the container log in real time.
ENV PYTHONUNBUFFERED=1

WORKDIR /sqlmesh/app

# For the dependency install below, so the build works from behind a corporate proxy.
# Scoped to this stage and not persisted in the image; empty means a direct connection.
ARG http_proxy
ARG https_proxy
ARG no_proxy

# A company-internal PyPI mirror uv tries in addition to PyPI. Scoped to this stage and
# not persisted in the image; empty means PyPI only.
ARG PYTHON_INDEX

# First, so the install below stays cached.
COPY sqlmesh/pyproject.toml /sqlmesh/pyproject.toml

# The dependencies from pyproject.toml.
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /sqlmesh --no-dev

# The same list the database image gets: a query on config.py's DuckDB gateway has to reach
# the same extensions a tenant reaches through use_duckdb(). Without a default, the list
# living in DUCKDB_EXTENSIONS in buildtime.env.
ARG DUCKDB_EXTENSIONS

# Kept in the image so config.py reads the list back rather than repeating it.
ENV DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}

# Pinned so the directory installed into below is the one DuckDB looks in at runtime. It
# cannot be moved with `extension_directory`: SQLMesh loads a gateway's extensions before
# any DuckDB setting applies, so only DuckDB's own default is early enough.
ENV HOME=/root

# Installed with the interpreter that later loads them, so the files match its DuckDB
# version exactly -- the database image has to work that out of libduckdb.so. One statement
# per extension, so a failing download names it; an unset list stops the build here.
RUN test -n "${DUCKDB_EXTENSIONS}" \
      || { echo "DUCKDB_EXTENSIONS is empty; build via build.sh, which reads buildtime.env." >&2; exit 1; } \
    && for ext in $(echo "${DUCKDB_EXTENSIONS}" | tr ',' ' '); do \
         echo "Installing DuckDB extension: ${ext}"; \
         uv run --project /sqlmesh python -c \
           "import duckdb; duckdb.connect().execute('INSTALL ${ext} FROM community')" || exit 1; \
       done

# Not the operator's to choose: it is how the gateway attaches PostgreSQL. A core
# extension rather than a community one, hence the missing FROM.
RUN uv run --project /sqlmesh python -c \
      "import duckdb; duckdb.connect().execute('INSTALL postgres')"

# The engine logs to stdout and writes its files under /tmp, and the password comes from
# the mounted secret, so a developer's sqlmesh/logs/ and sqlmesh/.env have no business
# here. .dockerignore keeps them out of the context; the removal below is a second line of
# defence for the password.
COPY sqlmesh/ /sqlmesh/app/
RUN rm -rf /sqlmesh/app/logs /sqlmesh/app/.env

# Committed executable, so a plain COPY preserves the mode without BuildKit's --chmod.
COPY sqlmesh/entrypoint.sh /sqlmesh/entrypoint.sh

CMD ["/sqlmesh/entrypoint.sh"]
