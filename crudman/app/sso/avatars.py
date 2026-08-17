"""The profile picture the provider publishes, shown beside the person's name.

Unfold's sidebar renders `user.avatar_url` and falls back to the initial of the name when
there is none, so the whole job here is finding that URL and putting it on the user.
Django's user model has no field for it and this project does not swap the model for one
of its own, so it is attached for the request rather than stored on the row.

The URL is the OpenID Connect `picture` claim, which arrives with the rest of the claims
under the `profile` scope -- no second API call, and nothing specific to one directory.

Providers differ in what they publish there, and the two cases are handled differently:

  * A link a browser may follow, which is what Keycloak, Authentik, Okta and Google
    publish. It is handed to Unfold as it stands and the browser fetches it.
  * A link only the provider's own API will honour, which is what Entra ID publishes: a
    Microsoft Graph address that answers 401 to a browser. That one is downloaded here,
    during the login, because the access token it needs exists only then.
"""
from base64 import b64decode, b64encode
from urllib.parse import urlparse

import requests
from django.urls import reverse

# Where the login leaves what the pages after it need. The URL is what the sidebar draws;
# the picture is present only in the second case above, and is what avatar() serves. Both
# live in the session, so both are gone the moment it ends.
SESSION_KEY = "avatar_url"
SESSION_PICTURE_KEY = "avatar_picture"

# OpenID Connect Core 1.0, section 5.1: "URL of the End-User's profile picture. This URL
# MUST refer to an image file (for example, a PNG, JPEG, or GIF image file)."
PICTURE_CLAIM = "picture"

# Long enough for a picture host to answer, short enough not to hold up a login that has
# otherwise already succeeded.
PROBE_TIMEOUT = 5

# A downloaded picture travels in the session row, which Django reads back on every single
# request, so there is a limit to what is worth carrying for something drawn at 38 pixels.
# Past it the initial is shown instead.
MAX_PICTURE_BYTES = 256 * 1024

# How long the browser may keep a downloaded picture. It is replaced at the next login, and
# the address it is served from does not change, so this is also how stale a face that was
# changed at the provider can be.
PICTURE_MAX_AGE = 3600


def claimed_picture(extra_data):
    """The picture URL the provider sent, out of what allauth stored for the account.

    Userinfo is read first, that being where the claim belongs and where a provider
    sending it at all will have put it. The mirror image of claimed_roles, which reads the
    ID token first for the reason given there.
    """
    data = extra_data or {}
    for source in (data.get("userinfo"), data.get("id_token"), data):
        if isinstance(source, dict) and source.get(PICTURE_CLAIM):
            return source[PICTURE_CLAIM]
    return None


def loadable_picture(url):
    """`url` if the browser will be able to fetch it for itself, and None if it will not.

    The claim is a link, and not every link is one a browser may follow. Unfold sets the
    URL as a CSS background, and a background that fails to load leaves an empty circle --
    worse than the initial it replaced -- so a link that cannot be fetched without
    credentials is not handed on. What happens to it instead is fetched_picture's subject.

    Asking from here is not quite the question, the browser being the one that will fetch
    it, but it is the closest thing to an answer available at login and it errs the right
    way: an unreachable picture host costs the picture, never the sidebar.
    """
    if not url:
        return None

    try:
        # Streamed, so the headers settle it and the image itself is never downloaded.
        with requests.get(url, stream=True, timeout=PROBE_TIMEOUT) as answer:
            is_image = answer.ok and answer.headers.get("content-type", "").startswith(
                "image/"
            )
    except requests.RequestException:
        return None

    return url if is_image else None


def fetched_picture(url, token, api_host):
    """The picture itself, downloaded with the token this login has just been given.

    Entra ID publishes a picture only its own API will hand over, and the access token
    that opens it is never stored (see SOCIALACCOUNT_STORE_TOKENS, and allauth holds it in
    memory whether or not it is). The login is therefore the one moment the picture can be
    had at all, which is why it is taken here rather than when a page comes to draw it.

    The token goes to `api_host` and nowhere else. That is the host allauth has just sent
    this very token to for the claims themselves, so nothing new is trusted with it, and a
    claim naming somewhere else is left to the browser or dropped -- a tampered or
    mistaken one cannot turn a login into a token handed to a stranger.
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
            # One byte past the limit, which is all it takes to know it was exceeded and
            # keeps a picture of any size from being read into memory to be rejected.
            data = answer.raw.read(MAX_PICTURE_BYTES + 1, decode_content=True)
    except requests.RequestException:
        return None

    if len(data) > MAX_PICTURE_BYTES:
        return None

    # Base64 because session data is stored as JSON, which has no way to carry bytes.
    return {"content_type": content_type, "data": b64encode(data).decode()}


def api_host(request, sociallogin):
    """The host the provider serves its API from, out of its discovery document.

    Read rather than configured, and read from the document the provider publishes about
    itself, so that the one host this project will send an access token to is the one the
    provider named -- for Entra ID graph.microsoft.com, which is where its userinfo
    endpoint lives and where it points the picture claim as well.
    """
    try:
        provider = sociallogin.account.get_provider(request)
        endpoint = provider.get_oauth2_adapter(request).openid_config["userinfo_endpoint"]
    except (KeyError, requests.RequestException):
        return None
    return urlparse(endpoint).netloc


def remember_picture(request, sociallogin=None, **kwargs):
    """Find the picture once per login, on allauth's user_logged_in signal.

    A login that named no provider carries no sociallogin and so no picture. Writing the
    two keys either way is deliberate: it clears a picture left behind by whoever held the
    session before.
    """
    url = claimed_picture(sociallogin.account.extra_data) if sociallogin else None
    picture = None

    if url and not loadable_picture(url):
        # The browser cannot have it, so this server asks for it on the browser's behalf,
        # and what it gets back is served from an address of our own.
        token = getattr(sociallogin.token, "token", None)
        if token:
            picture = fetched_picture(url, token, api_host(request, sociallogin))
        url = reverse("avatar") if picture else None

    request.session[SESSION_KEY] = url
    request.session[SESSION_PICTURE_KEY] = picture


def middleware(get_response):
    """Hand Unfold the picture that the login found.

    Only when there is one. Assigning to request.user resolves the lazy object Django
    leaves there, which would mean a query for the user on every request, including the
    many that never render a sidebar.
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
