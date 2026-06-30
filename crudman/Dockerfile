FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

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

# Copy the startup script that applies migrations before starting the server. It is
# committed executable, so a plain COPY (no BuildKit --chmod) preserves the mode.
COPY crudman/entrypoint.sh /crudman/entrypoint.sh

# Document the main application port.
EXPOSE 8000

CMD ["/crudman/entrypoint.sh"]

