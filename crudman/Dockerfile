# The registry the base image is pulled from (GHCR_IO_MIRROR in buildtime.env).
ARG GHCR_IO_MIRROR=ghcr.io

# Exported here rather than in the sqlmesh image, the two being built independently.
# Loading a SQLMesh Context parses the project files and opens no connection, so this needs
# nothing else; a separate stage keeps SQLMesh out of the shipped image.
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim AS docs

ARG http_proxy
ARG https_proxy
ARG no_proxy
ARG PYTHON_INDEX

COPY sqlmesh/pyproject.toml /sqlmesh/pyproject.toml
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /sqlmesh --no-dev

# The library the lineage graph is drawn with, fetched here rather than by the browser so
# the page renders on a server with no outbound internet. In this stage because it already
# reaches the network, and with Python because the base image carries no curl. Unpinned:
# tests/test_lineage_chart.py renders the shipped bundle, so a broken release fails there.
RUN python -c "import urllib.request as r; r.urlretrieve('https://unpkg.com/echarts@latest/dist/echarts.min.js', '/echarts.min.js')"

COPY sqlmesh/ /sqlmesh/app/

# config.py takes its developer branch here, the secret marking the deployed engine being
# mounted at run time. That branch reads the two env files and a developer's password, so
# both are copied in and a value supplied; nothing connects, so they need only exist.
# Beside the project rather than in it: config.py resolves them from its parent's parent.
COPY buildtime.env runtime.env /sqlmesh/
RUN SQLMESH_PASSWORD=unused uv run --project /sqlmesh \
      python /sqlmesh/app/docs_export.py /sqlmesh/app /docs.json


FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim

# The Python process is invoked once, so bytecode files earn nothing.
ENV PYTHONDONTWRITEBYTECODE=1

# Write stdout/stderr immediately, so errors appear in the container log in real time.
ENV PYTHONUNBUFFERED=1

WORKDIR /crudman/app

# For the dependency install below, so the build works from behind a corporate proxy.
# Scoped to this stage and not persisted in the image; empty means a direct connection.
ARG http_proxy
ARG https_proxy
ARG no_proxy

# A company-internal PyPI mirror uv tries in addition to PyPI. Scoped to this stage and
# not persisted in the image; empty means PyPI only.
ARG PYTHON_INDEX

# First, so the install below stays cached.
COPY crudman/pyproject.toml /crudman/pyproject.toml

# The dependencies from pyproject.toml.
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /crudman --no-dev

# Copy the Django application into the image.
COPY crudman/app/ /crudman/app/

# What the docs views read, exported by the stage above.
COPY --from=docs /docs.json /crudman/docs.json

# Into the docs app's static directory, so collectstatic picks it up and the template
# reaches it through {% static %}, the storage backend hashing the name.
COPY --from=docs /echarts.min.js /crudman/app/docs/static/docs/echarts.min.js

# Committed executable, so a plain COPY preserves the mode without BuildKit's --chmod.
COPY crudman/entrypoint.sh /crudman/entrypoint.sh

# Document the main application port.
EXPOSE 8000

CMD ["/crudman/entrypoint.sh"]

