FROM pgduckdb/pgduckdb:17-main

# Prevent Python from writing .pyc files. Since in a container, the Python process is
# invoked only once, there is no need to write bytecode files.
ENV PYTHONDONTWRITEBYTECODE=1

# Forces Python to write stdout/stderr immediately.
# This is useful for Docker logs and makes errors appear in real time.
ENV PYTHONUNBUFFERED=1

# Install uv for package management.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

USER root

# Use a cache directory under the runtime user's home so the interpreter is
# accessible when the container starts as the postgres user.
ENV HOME=/home/postgres
ENV UV_CACHE_DIR=/home/postgres/.cache/uv
ENV XDG_CACHE_HOME=/home/postgres/.cache
RUN mkdir -p /home/postgres/.cache && chown -R postgres:postgres /home/postgres

WORKDIR /crudman

# Copy dependency metadata first so Docker can cache dependency installation.
COPY pyproject.toml /crudman/pyproject.toml

# Install the project dependencies defined in pyproject.toml.
RUN uv sync --project /crudman --no-dev

# Copy the Django application into the image.
COPY crudman/ /crudman/

# Generate the static files used by Django admin and Unfold at build time.
RUN uv run --project /crudman python /crudman/manage.py collectstatic --noinput

# Ensure the project and cache can be used by the non-root runtime user.
RUN chown -R postgres:postgres /crudman /home/postgres

# Setup PostgreSQL using the /docker-entrypoint-initdb.d/ entry point. PostgreSQL will
# run the scripts when the container starts.
COPY --chown=postgres:postgres postgresql/ /docker-entrypoint-initdb.d/

USER postgres

EXPOSE 8000

# Start the application with Gunicorn.
CMD ["uv", "run", "--project", "/crudman", "gunicorn", "-b", "0.0.0.0:8000", "crudman.wsgi:application"]

