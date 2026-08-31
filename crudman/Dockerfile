# The registry the base image is pulled from, so a company mirror can stand in for a
# slow-to-reach ghcr.io (see GHCR_IO_MIRROR in buildtime.env).
ARG GHCR_IO_MIRROR=ghcr.io

# The model documentation is exported here rather than in the sqlmesh image, because the
# two images are built independently and neither can copy from the other. Loading a
# SQLMesh Context parses the project files and opens no connection, so the export needs
# nothing but the project itself. A separate stage keeps SQLMesh and its dependencies out
# of the shipped image: only the resulting JSON is copied on.
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim AS docs

ARG http_proxy
ARG https_proxy
ARG no_proxy
ARG PYTHON_INDEX

COPY sqlmesh/pyproject.toml /sqlmesh/pyproject.toml
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /sqlmesh --no-dev

# The charting library the lineage graph on the documentation page is drawn with. Fetched
# here rather than by the browser so the page renders on a server with no outbound
# internet, the same arrangement as the DuckDB extensions and the Grafana plugins. In this
# stage because it already reaches the network, and with Python because the base image
# carries no curl. Unpinned: run-tests.sh renders the shipped bundle and asserts the
# lineage graph still draws (tests/test_lineage_chart.py), so a release that broke it
# fails the suite rather than reaching a server.
RUN python -c "import urllib.request as r; r.urlretrieve('https://unpkg.com/echarts@latest/dist/echarts.min.js', '/echarts.min.js')"

COPY sqlmesh/ /sqlmesh/app/

# config.py takes its developer branch here: the podman secret that marks the deployed
# engine is mounted at run time, not during a build. That branch reads the two env files
# next to the project and the password of a developer's own account, so both are copied
# in and a value is supplied. Nothing connects -- loading the project only parses it --
# so the values need only exist.
# Beside the project rather than in it: config.py resolves them from its own parent's
# parent, which is /sqlmesh once the project sits in /sqlmesh/app.
COPY buildtime.env runtime.env /sqlmesh/
RUN SQLMESH_PASSWORD=unused uv run --project /sqlmesh \
      python /sqlmesh/app/docs_export.py /sqlmesh/app /docs.json


FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim

# Prevent Python from writing .pyc files. Since in a container, the Python process is
# invoked only once, there is no need to write bytecode files.
ENV PYTHONDONTWRITEBYTECODE=1

# Forces Python to write stdout/stderr immediately.
# This is useful for Docker logs and makes errors appear in real time.
ENV PYTHONUNBUFFERED=1

WORKDIR /crudman/app

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
COPY crudman/pyproject.toml /crudman/pyproject.toml

# Install the project dependencies defined in pyproject.toml.
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /crudman --no-dev

# Copy the Django application into the image.
COPY crudman/app/ /crudman/app/

# The model documentation exported by the stage above, which the docs views read.
COPY --from=docs /docs.json /crudman/docs.json

# Into the docs app's static directory, so collectstatic picks it up with everything else
# and the template reaches it through {% static %} -- the storage backend hashes the name.
COPY --from=docs /echarts.min.js /crudman/app/docs/static/docs/echarts.min.js

# Copy the startup script that applies migrations before starting the server. It is
# committed executable, so a plain COPY (no BuildKit --chmod) preserves the mode.
COPY crudman/entrypoint.sh /crudman/entrypoint.sh

# Document the main application port.
EXPOSE 8000

CMD ["/crudman/entrypoint.sh"]

