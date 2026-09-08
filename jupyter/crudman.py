"""Authenticating a notebook visitor against the admin panel's session.

crudman already knows who everyone is and what rank the identity provider gave them. So
rather than configuring a second OpenID Connect client here -- which would need its own
registration, its own claim mapping and a second sign-in for the person -- the hub presents
the visitor's own crudman cookie to crudman and believes the answer.

The consequences are the point: single sign-on is configured once, local accounts work
identically when it is off, a demotion reaches the notebooks, and this container holds no
credential that could ask about anybody but the caller.
"""
import json
import os
import re
from urllib.parse import urlencode

from jupyterhub.auth import LocalAuthenticator
from jupyterhub.handlers import BaseHandler
from tornado.httpclient import AsyncHTTPClient, HTTPClientError
from tornado.web import HTTPError

CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")
WHOAMI_URL = f"http://127.0.0.1:8000/{CRUDMAN_PATH}/notebooks/whoami/"
"""The admin panel, on the localhost the pod shares. Its own port, not the proxy's: the
request is already inside the pod and needs no second TLS hop."""

SESSION_COOKIE = os.environ.get("SESSION_COOKIE_NAME", "sessionid")
"""The cookie Django's session middleware sets."""


async def identify(cookie: str, rotate_credential: bool = False) -> dict:
    """Ask crudman who holds this session, and optionally for a database credential.

    Args:
        cookie: The value of the visitor's crudman session cookie.
        rotate_credential: Whether to also rotate and return the notebook database login,
            which crudman does on POST. Only at spawn time, so a page view does not.

    Returns:
        The identity crudman answered with.

    Raises:
        HTTPError: 403 when the session names nobody or holds too low a rank, carrying
            crudman's own words -- it is the one that knows why.
    """
    client = AsyncHTTPClient()
    try:
        response = await client.fetch(
            WHOAMI_URL,
            method="POST" if rotate_credential else "GET",
            body=b"" if rotate_credential else None,
            headers={"Cookie": f"{SESSION_COOKIE}={cookie}"},
        )
    except HTTPClientError as error:
        detail = "the administration panel refused this session"
        if error.response is not None and error.response.body:
            try:
                detail = json.loads(error.response.body).get("detail", detail)
            except ValueError:
                pass
        raise HTTPError(403, detail)

    return json.loads(response.body)


class CrudmanLoginHandler(BaseHandler):
    """Signs a visitor in from the cookie they already carry, or sends them to get one."""

    async def get(self):
        """Complete the login, or redirect to the admin panel's sign-in page.

        A visitor with no crudman session is sent there with ``next`` pointing back here,
        so single sign-on takes them to the provider and returns them to the notebook they
        asked for. One hop, whether single sign-on is on or off.

        Someone who *has* a session and is still refused is told so, rather than sent back
        to sign in: they would return with the same session and be refused again, which is
        a redirect loop rather than an answer.
        """
        user = await self.login_user()
        if user is not None:
            self.redirect(self.get_next_url(user))
            return

        if self.get_cookie(SESSION_COOKIE):
            raise HTTPError(
                403,
                "Your account may not use the notebooks. They are open from the editor "
                "rank upwards, and an administrator grants that.",
            )

        target = self.get_argument("next", self.hub.server.base_url)
        self.redirect(f"/{CRUDMAN_PATH}/login/?{urlencode({'next': target})}")


class CrudmanAuthenticator(LocalAuthenticator):
    """Takes crudman's word for who a visitor is and whether they may be here.

    Local, because each server runs as a Unix account of its own: LocalAuthenticator
    creates it on first sign-in, named as crudman names the person's database role.
    """

    # No login form of our own: the credential is a cookie the browser already holds, and
    # asking for a password would be asking for one this system cannot check.
    auto_login = True

    # The person exists in crudman, not here, so their account is made on arrival. Their
    # home is what /etc/skel seeds: the DuckDB extensions and the kernel startup file.
    create_system_users = True

    def get_handlers(self, app):
        return [(r"/login", CrudmanLoginHandler)]

    def normalize_username(self, username):
        """The Unix account and database role name for a Django username.

        The same derivation as ``dbusers.utils.role_name_for`` in crudman, which is the
        point: the server runs under this name, so ``getpass.getuser()`` in the SQLMesh
        config resolves to the role the person actually connects as. A username from an
        identity provider is frequently an email address and no valid identifier.

        Args:
            username: The Django username.

        Returns:
            The prefixed slug, capped as the database function caps it.
        """
        slug = re.sub(r"[^a-z0-9]+", "_", username.strip().lower()).strip("_")
        return f"{os.environ.get('DB_USER_PREFIX', '')}{slug}"[:50]

    async def authenticate(self, handler, data=None):
        """Identify the visitor from their crudman session cookie.

        Args:
            handler: The request handler, whose cookies carry the session.
            data: The login form data, always None here.

        Returns:
            The user record, its auth_state holding the cookie so the spawn hook can
            present it again; None when the visitor carries no session, which sends them to
            sign in.
        """
        cookie = handler.get_cookie(SESSION_COOKIE)
        if not cookie:
            return None

        identity = await identify(cookie)
        return {
            "name": identity["username"],
            "admin": identity["admin"],
            "auth_state": {"session": cookie, "identity": identity},
        }

    async def refresh_user(self, user, handler=None):
        """Re-check the rank of someone whose session is already open.

        Args:
            user: The hub's user record.
            handler: The request handler, unused.

        Returns:
            False when the stored session no longer identifies an editor, which makes the
            hub ask them to sign in again; the unchanged record otherwise.
        """
        state = await user.get_auth_state()
        if not state:
            return False
        try:
            await identify(state["session"])
        except HTTPError:
            return False
        return True

    async def pre_spawn_start(self, user, spawner):
        """Put a freshly rotated database credential into the server's environment.

        Called with the person's own session, so crudman provisions the credential for
        them and for nobody else. It reaches the process through the environment and is
        written nowhere, expiring at the next spawn.

        Args:
            user: The hub's user record.
            spawner: The spawner about to start their server.
        """
        state = await user.get_auth_state()
        identity = await identify(state["session"], rotate_credential=True)

        # A fresh dict per spawn: Spawner.environment defaults to the one object shared
        # from the configuration, and updating that in place would leak one person's
        # credential into the next person's server.
        spawner.environment = {
            **spawner.environment,
            # The person's own role, so current_user in the notebook is them.
            "SQLMESH_USER": identity["db_user"],
            "SQLMESH_PASSWORD": identity["db_password"],
            # What the git panel signs a commit with, so history names a person rather
            # than a Unix account.
            "GIT_AUTHOR_NAME": identity["name"],
            "GIT_AUTHOR_EMAIL": identity["email"],
            "GIT_COMMITTER_NAME": identity["name"],
            "GIT_COMMITTER_EMAIL": identity["email"],
        }
