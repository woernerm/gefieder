FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

# Prevent Python from writing .pyc files. Since in a container, the Python process is
# invoked only once, there is no need to write bytecode files.
ENV PYTHONDONTWRITEBYTECODE=1

# Forces Python to write stdout/stderr immediately.
# This is useful for Docker logs and makes errors appear in real time.
ENV PYTHONUNBUFFERED=1

WORKDIR /crudman/app

# Copy dependency metadata first so Docker can cache dependency installation.
COPY crudman/pyproject.toml /crudman/pyproject.toml

# Install the project dependencies defined in pyproject.toml.
RUN uv sync --project /crudman --no-dev

# Copy the Django application into the image.
COPY crudman/app/ /crudman/app/

# Generate the static files used by Django admin and Unfold at build time.
RUN uv run --project /crudman python /crudman/app/manage.py collectstatic --noinput

# Document the main application port.
EXPOSE 8000

CMD ["uv", "run", "--project", "/crudman", "gunicorn", "-b", "0.0.0.0:8000", "crudman.wsgi:application"]

