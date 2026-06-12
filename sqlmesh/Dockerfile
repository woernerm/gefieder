FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

# Prevent Python from writing .pyc files. Since in a container, the Python process is
# invoked only once, there is no need to write bytecode files.
ENV PYTHONDONTWRITEBYTECODE=1

# Forces Python to write stdout/stderr immediately.
# This is useful for Docker logs and makes errors appear in real time.
ENV PYTHONUNBUFFERED=1

WORKDIR /sqlmesh/app

# Copy dependency metadata first so Docker can cache dependency installation.
COPY sqlmesh/pyproject.toml /sqlmesh/pyproject.toml

# Install the project dependencies defined in pyproject.toml.
RUN uv sync --project /sqlmesh --no-dev

# Copy the SQLMesh project into the image.
COPY sqlmesh/ /sqlmesh/app/

# Copy the startup script that applies the plan and runs the scheduling loop.
COPY --chmod=755 sqlmesh/entrypoint.sh /sqlmesh/entrypoint.sh

CMD ["/sqlmesh/entrypoint.sh"]
