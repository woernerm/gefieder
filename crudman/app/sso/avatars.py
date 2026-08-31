"""The profile picture the provider publishes, shown beside the person's name.

Unfold's sidebar renders ``user.avatar_url``, so the job is finding that URL and attaching
it to the user for the request. The URL is the OpenID Connect ``picture`` claim, and
providers publish two kinds of link there:

  * One a browser may follow (Keycloak, Authentik, Okta, Google), handed on as it stands.
  * One only the provider's own API will honour (Entra ID's Microsoft Graph address).
    That one is downloaded during the login, the only moment its token exists.
"""
from base64 import b64decode, b64encode
from urllib.parse import urlparse

import requests
from django.urls import reverse

SESSION_KEY = "avatar_url"
"""Session key holding the URL the sidebar draws; gone the moment the session ends."""

SESSION_PICTURE_KEY = "avatar_picture"
"""Session key holding a downloaded picture, which ``avatar()`` then serves."""

PICTURE_CLAIM = "picture"
"""The OpenID Connect claim naming the end user's profile picture (Core 1.0, 5.1)."""

PROBE_TIMEOUT = 5
"""Seconds a picture host gets to answer, short enough not to hold up a login."""

MAX_PICTURE_BYTES = 256 * 1024
"""Largest picture worth carrying in the session row, which Django reads on every
request. Past it, the initial is shown instead."""

PICTURE_MAX_AGE = 3600
"""Seconds the browser may keep a downloaded picture.

Also how stale a face changed at the provider can be: the address it is served from does
not change between logins.
"""


def claimed_picture(extra_data):
    """The picture URL the provider sent, out of what allauth stored for the account.

    Args:
        extra_data: The account's stored provider data.

    Returns:
        The URL, or None. Userinfo is read first, that being where the claim belongs --
        the mirror image of ``claimed_roles``.
    """
    data = extra_data or {}
    for source in (data.get("userinfo"), data.get("id_token"), data):
        if isinstance(source, dict) and source.get(PICTURE_CLAIM):
            return source[PICTURE_CLAIM]
    return None


def loadable_picture(url):
    """The URL if a browser will be able to fetch it for itself, otherwise None.

    Unfold sets the URL as a CSS background, and one that fails to load leaves an empty
    circle, worse than the initial it replaced. Probing from the server is not the same
    question, but it errs the right way: an unreachable host costs only the picture.

    Args:
        url: The picture URL the provider claimed.

    Returns:
        The URL if it answers as an image, otherwise None; ``fetched_picture`` then
        takes over.
    """
    if not url:
        return None

    try:
        # Streamed: the headers settle it and the image is never downloaded.
        with requests.get(url, stream=True, timeout=PROBE_TIMEOUT) as answer:
            is_image = answer.ok and answer.headers.get("content-type", "").startswith(
                "image/"
            )
    except requests.RequestException:
        return None

    return url if is_image else None


def fetched_picture(url, token, api_host):
    """The picture itself, downloaded with the token this login has just been given.

    The access token is never stored (SOCIALACCOUNT_STORE_TOKENS), so the login is the
    one moment the picture can be had.

    Args:
        url: The claimed picture URL.
        token: The access token this login was given.
        api_host: The only host the token may go to, being the one allauth just sent it
            to for the claims. A tampered claim cannot hand the token to a stranger.

    Returns:
        The picture as ``{"content_type", "data"}``, the data base64-encoded because
        session data is JSON; None if it cannot be had or is too large.
    """
    if not api_host or urlparse(url).netloc != api_host:
        return None

    try:
        with requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            stream=True,
            timeout=PROBE_TIMEOUT,
        ) as answer:
            content_type = answer.headers.get("content-type", "")
            if not answer.ok or not content_type.startswith("image/"):
                return None
            # One byte past the limit proves it was exceeded, without reading a picture
            # of any size into memory only to reject it.
            data = answer.raw.read(MAX_PICTURE_BYTES + 1, decode_content=True)
    except requests.RequestException:
        return None

    if len(data) > MAX_PICTURE_BYTES:
        return None

    return {"content_type": content_type, "data": b64encode(data).decode()}


def api_host(request, sociallogin):
    """The host the provider serves its API from, out of its discovery document.

    Read rather than configured, so the one host an access token goes to is the one the
    provider named for its own userinfo endpoint.

    Args:
        request: The request the login is running on.
        sociallogin: allauth's login object.

    Returns:
        The host name, or None if the document cannot be read.
    """
    try:
        provider = sociallogin.account.get_provider(request)
        endpoint = provider.get_oauth2_adapter(request).openid_config["userinfo_endpoint"]
    except (KeyError, requests.RequestException):
        return None
    return urlparse(endpoint).netloc


def remember_picture(request, sociallogin=None, **kwargs):
    """Find the picture once per login, on allauth's user_logged_in signal.

    Args:
        request: The request the login is running on.
        sociallogin: allauth's login object, absent for a login that named no provider.
            Both session keys are written either way, clearing any predecessor's picture.
        **kwargs: The remaining signal arguments, all unused.
    """
    url = claimed_picture(sociallogin.account.extra_data) if sociallogin else None
    picture = None

    if url and not loadable_picture(url):
        # The browser cannot have it, so this server fetches it and serves it back from
        # an address of our own.
        token = getattr(sociallogin.token, "token", None)
        if token:
            picture = fetched_picture(url, token, api_host(request, sociallogin))
        url = reverse("avatar") if picture else None

    request.session[SESSION_KEY] = url
    request.session[SESSION_PICTURE_KEY] = picture


def middleware(get_response):
    """Hand Unfold the picture that the login found, when there is one.

    Only when there is one: assigning to ``request.user`` resolves the lazy object Django
    leaves there, a query on every request including those rendering no sidebar.

    Args:
        get_response: The next handler in the middleware chain.

    Returns:
        The middleware callable.
    """
    def attach_picture(request):
        if url := request.session.get(SESSION_KEY):
            request.user.avatar_url = url
        return get_response(request)

    return attach_picture


def stored_picture(session):
    """The downloaded picture and its type, ready for an HTTP response."""
    picture = session.get(SESSION_PICTURE_KEY)
    if not picture:
        return None
    return b64decode(picture["data"]), picture["content_type"]
