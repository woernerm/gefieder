# The registries the base images are pulled from (buildtime.env), and the hub version.
# All three are used in a FROM, so they are declared before the first stage; a value
# needed inside a stage is repeated there.
ARG DOCKER_IO_MIRROR=docker.io
ARG GHCR_IO_MIRROR=ghcr.io

# Pinned: jupyterhub is installed into the notebook environment below and has to match the
# hub running here, and an unpinned base would drift out from under it.
ARG JUPYTERHUB_VERSION=5.5.1

# uv itself, copied in below rather than downloaded, so the build needs no second network
# source and the version matches the one every other image here uses.
FROM ${GHCR_IO_MIRROR}/astral-sh/uv:python3.13-trixie-slim AS uv

# The hub's own image: it carries JupyterHub, node and configurable-http-proxy, which is
# the one dependency this stack cannot build from Python alone. Taking it from the
# JupyterHub team means node is their problem rather than an npm registry setting here.
FROM ${DOCKER_IO_MIRROR}/jupyterhub/jupyterhub:${JUPYTERHUB_VERSION}

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG http_proxy
ARG https_proxy
ARG no_proxy
ARG PYTHON_INDEX

COPY --from=uv /usr/local/bin/uv /usr/local/bin/uv

# git is what the notebooks ship models with, and the panel in Lab drives this binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# The environment every notebook server runs in, kept apart from the hub's own: the hub
# image is Python 3.12 and the SQLMesh project needs 3.13, so uv brings its own interpreter
# rather than this image dictating one to the models.
ENV NOTEBOOK_VENV=/opt/notebook

# Where uv puts the interpreter it downloads. Its default is under $HOME, which is /root
# and unreadable to anyone else, and every notebook server runs as its own Unix user: the
# venv below symlinks that interpreter, so the spawn fails with "permission denied" on a
# path nothing in the error names.
ENV UV_PYTHON_INSTALL_DIR=/opt/python
COPY sqlmesh/pyproject.toml /opt/notebook-project/pyproject.toml
COPY jupyter/requirements.txt /opt/notebook-project/requirements.txt

# Repeated after the FROM: an ARG declared before it is out of scope here.
ARG JUPYTERHUB_VERSION

# SQLMesh from the engine's own pyproject.toml, so a notebook plans with the version
# production runs -- a difference there would show up as a plan that works in one place and
# not the other. Lab and its extensions from requirements.txt beside it.
#
# jupyterhub itself goes in as well, pinned to the base image's version: it is what provides
# jupyterhub-singleuser, the server spawned per person, and that has to run in *this*
# environment because it imports JupyterLab. PATH below puts this environment first, so it
# is the one the hub finds.
RUN UV_EXTRA_INDEX_URL=${PYTHON_INDEX} uv venv --python 3.13 ${NOTEBOOK_VENV} \
    && UV_EXTRA_INDEX_URL=${PYTHON_INDEX} VIRTUAL_ENV=${NOTEBOOK_VENV} \
       uv pip install --requirement /opt/notebook-project/pyproject.toml \
    && UV_EXTRA_INDEX_URL=${PYTHON_INDEX} VIRTUAL_ENV=${NOTEBOOK_VENV} \
       uv pip install --requirement /opt/notebook-project/requirements.txt \
            "jupyterhub==${JUPYTERHUB_VERSION}"

# The extensions an operator adds in buildtime.env, installed the way the Grafana plugins
# and DuckDB extensions are: at build time, so the target machine needs no package index.
# Empty is normal -- what the workflow depends on is in requirements.txt above.
ARG JUPYTER_EXTENSIONS
RUN if [ -n "${JUPYTER_EXTENSIONS}" ]; then \
      UV_EXTRA_INDEX_URL=${PYTHON_INDEX} VIRTUAL_ENV=${NOTEBOOK_VENV} \
        uv pip install $(echo "${JUPYTER_EXTENSIONS}" | tr ',' ' ') || exit 1; \
    fi

# The same list the database and engine images get, so a query on config.py's DuckDB
# gateway reaches the extensions a model asks for without downloading them.
ARG DUCKDB_EXTENSIONS
ENV DUCKDB_EXTENSIONS=${DUCKDB_EXTENSIONS}

# Installed into /etc/skel, which useradd copies into each person's home: DuckDB looks in
# $HOME and nothing earlier than that default applies (see sqlmesh/Dockerfile).
RUN test -n "${DUCKDB_EXTENSIONS}" \
      || { echo "DUCKDB_EXTENSIONS is empty; build via build.sh, which reads buildtime.env." >&2; exit 1; } \
    && for ext in $(echo "${DUCKDB_EXTENSIONS}" | tr ',' ' ') ; do \
         echo "Installing DuckDB extension: ${ext}"; \
         HOME=/etc/skel ${NOTEBOOK_VENV}/bin/python -c \
           "import duckdb; duckdb.connect().execute('INSTALL ${ext} FROM community')" || exit 1; \
       done \
    && HOME=/etc/skel ${NOTEBOOK_VENV}/bin/python -c \
         "import duckdb; duckdb.connect().execute('INSTALL postgres')"

# Both are read and executed by every person's server, which runs as their own account.
RUN chmod -R a+rX /opt/python ${NOTEBOOK_VENV}

# The kernel every model notebook runs on. Its language is "sql", which is what makes
# JupyterLab open a .sql model as a notebook: jupytext registers a file type per kernel
# language, and CodeMirror already maps that language to the .sql extension.
COPY jupyter/kernel/kernel.json /usr/local/share/jupyter/kernels/sqlmesh/kernel.json
RUN sed -i "s|\"python\"|\"${NOTEBOOK_VENV}/bin/python\"|" \
      /usr/local/share/jupyter/kernels/sqlmesh/kernel.json

# What that kernel has already done before the first cell: load the magics and open the
# project. Reached through exec_files in the IPython configuration, which a kernel does
# read -- unlike a profile's startup/ directory, which ipykernel never runs.
COPY jupyter/startup.py ${NOTEBOOK_VENV}/etc/sqlmesh_startup.py
# Beside it, because startup.py reads it relative to itself: the module it wraps around
# quak's own to reach inside the table's shadow root.
COPY jupyter/quaktheme/ ${NOTEBOOK_VENV}/etc/quaktheme/
RUN mkdir -p /etc/skel/.ipython/profile_default
COPY jupyter/ipython_kernel_config.py /etc/skel/.ipython/profile_default/ipython_kernel_config.py

# The system's colours, and the stylesheet that applies them to Lab. Read only when
# custom_css is enabled, which jupyter_server_config.py does. The palette is Grafana's,
# kept beside Grafana to say so; every image that themes itself copies it from there.
COPY grafana/palette.css jupyter/custom/custom.css /etc/skel/.jupyter/custom/

# What a person's working tree must never carry into a commit. Their own global ignore
# file, so the models repository's .gitignore stays the one this system ships.
COPY jupyter/gitignore /etc/skel/.gitignore
RUN git config --system core.excludesFile "~/.gitignore" \
    # A commit from the git panel is signed with the person's name and address, which the
    # spawn hook puts in the environment; without a fallback identity git refuses to commit
    # at all if that lookup ever fails.
    && git config --system user.name "Notebook user" \
    && git config --system user.email "notebooks@localhost" \
    # The workspace is owned by the person, but git also inspects the models volume above
    # it, which is root's.
    && git config --system --add safe.directory '*'

# The contents manager that makes a .sql model open as a notebook, and the kernel-side
# cell routing. On the notebook environment's path rather than installed as a package: it
# is configuration of this system, not a library anyone imports.
COPY jupyter/sqlnotebook/ ${NOTEBOOK_VENV}/lib/python3.13/site-packages/sqlnotebook/
COPY jupyter/jupyter_server_config.py /usr/local/etc/jupyter/jupyter_server_config.py
# Into the venv, which is JupyterLab's application directory here: overrides are read
# from there alone, so the same file under /usr/local is silently ignored and a model
# opens in the text editor instead.
COPY jupyter/overrides/overrides.json \
     ${NOTEBOOK_VENV}/share/jupyter/lab/settings/overrides.json

# The hub's own configuration and the two modules it imports, its page template, and the
# palette that template inlines.
COPY jupyter/jupyterhub_config.py jupyter/crudman.py jupyter/spawn.py grafana/palette.css /etc/jupyterhub/
COPY jupyter/templates/ /etc/jupyterhub/templates/
COPY jupyter/entrypoint.sh /jupyter/entrypoint.sh

# So the hub finds the single-user server, which lives in the notebook environment.
ENV PATH=${NOTEBOOK_VENV}/bin:${PATH}

EXPOSE 8888

CMD ["/jupyter/entrypoint.sh"]
