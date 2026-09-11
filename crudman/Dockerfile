# The registry the base image is pulled from (GHCR_IO_MIRROR in buildtime.env).
ARG GHCR_IO_MIRROR=ghcr.io

# One download, in a stage of its own so the network access and the proxy arguments stay
# out of the shipped image. It used to export the model documentation too; that now comes
# from the engine at deploy time, which is what removed a whole SQLMesh install from this
# build.
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim AS assets

ARG http_proxy
ARG https_proxy
ARG no_proxy

# The library the lineage graph is drawn with, fetched here rather than by the browser so
# the page renders on a server with no outbound internet. With Python because the base
# image carries no curl. Unpinned: tests/test_lineage_chart.py renders the shipped bundle,
# so a broken release fails there.
RUN python -c "import urllib.request as r; r.urlretrieve('https://unpkg.com/echarts@latest/dist/echarts.min.js', '/echarts.min.js')"


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

# crudman clones, fetches and checks out the models repository, and hosts the bare origin
# when REPO_MODELS is the local default. The git binary does all of it, so nothing here
# reimplements a repository format.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# First, so the install below stays cached.
COPY crudman/pyproject.toml /crudman/pyproject.toml

# The dependencies from pyproject.toml.
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv sync --project /crudman --no-dev

# Copy the Django application into the image.
COPY crudman/app/ /crudman/app/

# Into the docs app's static directory, so collectstatic picks it up and the template
# reaches it through {% static %}, the storage backend hashing the name.
COPY --from=assets /echarts.min.js /crudman/app/docs/static/docs/echarts.min.js

# The system's colours, the same way: Unfold's STYLES setting links it into every page,
# and its COLORS point into it. Grafana's palette, kept beside Grafana to say so.
COPY grafana/palette.css /crudman/app/system/static/css/palette.css

# What a models repository starts life as, used only when REPO_MODELS names a path that
# does not exist yet. Shipping the examples as the first commit is what keeps them worth
# having: the same tree a fresh installation runs is the one a developer clones, so the
# whole system can be exercised end to end before anyone writes a model of their own.
#
# The container-side tools are stripped: they belong to the image, not to the project, and
# a clone carrying them would invite someone to edit them.
COPY sqlmesh/ /seed/sqlmesh/
RUN rm -f /seed/sqlmesh/Dockerfile /seed/sqlmesh/entrypoint.sh /seed/sqlmesh/sqlmesh.sh \
          /seed/sqlmesh/docs_export.py /seed/sqlmesh/status.py

# What a developer's checkout produces and must never commit. The password lives in
# sqlmesh/.env for the same reason it does in this repository -- SQLMesh loads that file
# itself -- so it is named here rather than left to be remembered.
RUN printf '%s\n' \
      '.venv/' \
      '__pycache__/' \
      'sqlmesh/.env' \
      'sqlmesh/.cache/' \
      'sqlmesh/logs/' \
      '.ipynb_checkpoints/' \
      '.virtual_documents/' \
      > /seed/.gitignore

# Committed executable, so a plain COPY preserves the mode without BuildKit's --chmod.
COPY crudman/entrypoint.sh /crudman/entrypoint.sh

# Document the main application port.
EXPOSE 8000

CMD ["/crudman/entrypoint.sh"]

