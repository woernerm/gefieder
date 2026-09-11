"""JupyterHub, authenticating against crudman and spawning one JupyterLab per person.

There is no user directory here. A visitor arrives with the session cookie the admin panel
gave them, the hub asks crudman who that is, and crudman answers with their rank. So the
accounts, the sign-in and single sign-on are configured once, in one place, and a notebook
needs nothing of its own.

What each server gets is in ``spawn.py``: a working tree of the models repository, a
database credential rotated for this one session, and a SQLMesh kernel.
"""
import os

from crudman import CrudmanAuthenticator
from spawn import WorkspaceSpawner

c = get_config()  # noqa: F821 -- JupyterHub injects this into the file's namespace.

NOTEBOOK_VENV = os.environ.get("NOTEBOOK_VENV", "/opt/notebook")
"""The environment the notebook servers and their kernels run in, beside the hub's own."""

# --- how it looks -----------------------------------------------------------------------
# The hub's own pages in the system's colours. templates/page.html extends the stock one
# and inlines the palette, the same file the notebooks and the admin panel use; it is read
# here once rather than served, the hub having no static directory of its own to put it in.
# DEFAULT_THEME comes from runtime.env, as it does for Grafana and the admin panel.
# The stock directory stays on the list: the setting replaces the default rather than
# adding to it, and every page that is not overridden is still served from there.
c.JupyterHub.template_paths = [
    "/etc/jupyterhub/templates",
    f"{NOTEBOOK_VENV}/share/jupyterhub/templates",
]
c.JupyterHub.template_vars = {
    "app_palette": open("/etc/jupyterhub/palette.css").read(),
    "default_theme": "light" if os.environ.get("DEFAULT_THEME") == "light" else "dark",
}

# --- where it is reached ----------------------------------------------------------------
# The proxy forwards /${NOTEBOOK_PATH}/ here with the path unchanged, as it does for the
# admin panel and Grafana, so the hub serves from that prefix rather than the root. Carried
# in bind_url below rather than set on its own: setting both is what JupyterHub warns about.
BASE_URL = f"/{os.environ.get('NOTEBOOK_PATH', 'jupyter')}/"

# The containers share the pod's network namespace, so binding the loopback keeps the hub
# and the spawned servers off the pod's published ports: the nginx proxy is the only way in.
# One namespace also means one set of ports for the whole pod, so these three avoid the ones
# already taken: 8000 is crudman, 8001 the Grafana MCP server, 3000 Grafana, 5432 the
# database.
c.JupyterHub.bind_url = f"http://127.0.0.1:8888{BASE_URL}"
c.JupyterHub.hub_bind_url = "http://127.0.0.1:8081"

# The API of the routing proxy the hub spawns, whose own default is 8001.
c.ConfigurableHTTPProxy.api_url = "http://127.0.0.1:8082"

# Where a spawned server reaches the hub's API. Explicit because the default is built from
# hub_bind_url's host, which a single-user server started under a different user resolves
# the same way only by luck.
c.JupyterHub.hub_connect_url = "http://127.0.0.1:8081"

# --- who gets in ------------------------------------------------------------------------
c.JupyterHub.authenticator_class = CrudmanAuthenticator

# The state a person's session was authenticated with -- their crudman cookie -- is
# encrypted at rest with JUPYTERHUB_CRYPT_KEY, which the entrypoint derives from the podman
# secret. Needed because the spawn hook presents that cookie back to crudman.
c.Authenticator.enable_auth_state = True

# Authenticating *is* the authorization check here: crudman answers only for someone holding
# the editor rank and refuses everyone else, so a second allow-list would be a copy of a
# decision already made -- and JupyterHub 5 admits nobody without one.
c.Authenticator.allow_all = True

# A demotion in the identity provider reaches crudman at the person's next sign-in there;
# this is how it reaches a notebook that is already open. Five minutes rather than the
# default hour, and always before a spawn, so a withdrawn rank cannot start a fresh server.
c.Authenticator.auth_refresh_age = 300
c.Authenticator.refresh_pre_spawn = True

# --- what is spawned --------------------------------------------------------------------
c.JupyterHub.spawner_class = WorkspaceSpawner

# By absolute path, because a spawned process inherits none of this container's PATH: the
# notebook environment is a second interpreter beside the hub's own, and the single-user
# server has to be the one that has JupyterLab.
c.Spawner.cmd = [f"{NOTEBOOK_VENV}/bin/jupyterhub-singleuser"]

# What a spawned server inherits from this container. A spawn otherwise starts with almost
# nothing: PATH is how the kernel and git are found, and the four SQLMESH_* settings are
# what sqlmesh/config.py builds the connection from -- the quadlet puts them here, and
# without them a kernel comes up with no project loaded. The person's own credential is not
# among them; the spawn hook adds that per person.
c.Spawner.env_keep = [
    "PATH",
    "SQLMESH_HOST",
    "SQLMESH_PORT",
    "SQLMESH_DATABASE",
    "DUCKDB_EXTENSIONS",
    "MODELS_DIR",
]

# Straight into JupyterLab, and straight into a server: a person who may be here at all has
# nothing to decide on a "start my server" page. WorkspaceSpawner narrows this per spawn to
# the SQLMesh project inside the workspace, the server itself being rooted at the
# repository so the git panel can find it.
c.Spawner.default_url = "/lab"
c.JupyterHub.implicit_spawn_seconds = 1

# A cold start clones the models repository and installs the kernel, and a slow disk makes
# that longer than the 30s default.
c.Spawner.start_timeout = 120

# These servers share one virtual machine with PostgreSQL and the engine, so an abandoned
# one is not free. A day's inactivity, then the hub reclaims it; the workspace is on the
# volume, so nothing is lost.
c.JupyterHub.services = [
    {
        "name": "idle-culler",
        "command": [
            "jupyterhub-idle-culler",
            "--timeout=86400",
            "--cull-every=3600",
        ],
    }
]
# What the culler may do, rather than "admin": the four scopes its own documentation asks
# for -- see who is idle and stop their server, and nothing else. "admin:users" is the one
# it wants for --cull-users, which this does not pass: a person's account here is crudman's
# to remove, not a notebook service's.
c.JupyterHub.load_roles = [
    {
        "name": "idle-culler",
        "services": ["idle-culler"],
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
            "delete:servers",
        ],
    }
]

# --- state --------------------------------------------------------------------------
# Which servers are running and who has signed in. On the volume, so a restart does not
# strand a running server; losing it costs a re-login, not data, which is why this is
# sqlite and not a role in PostgreSQL.
c.JupyterHub.db_url = "sqlite:////var/lib/app/jupyter/jupyterhub.sqlite"
c.JupyterHub.cookie_secret_file = "/var/lib/app/jupyter/cookie_secret"

# journald stamps every line, so the hub's own timestamps would be a second copy. The
# proxy it spawns writes its own; nothing here can turn those off.
c.JupyterHub.log_datefmt = ""
c.JupyterHub.log_format = "%(levelname)s %(name)s %(message)s"
