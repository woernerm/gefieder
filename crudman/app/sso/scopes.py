"""What a sign-in asks the provider for.

The three standard OpenID Connect scopes carry every claim this project reads. The
profile picture is the exception: some providers keep it behind their own API and want a
permission of their own before parting with it.

Which permission cannot be discovered — Entra ID's document advertises neither User.Read
(a Microsoft Graph permission rather than a scope) nor the ``picture`` claim its userinfo
endpoint returns — so the provider is recognised by its configured issuer instead.
Asking everywhere is not an option: a provider refuses an authorization request naming a
scope it has never heard of, so a misplaced scope costs the sign-in, not just a picture.
"""
from urllib.parse import urlparse

STANDARD_SCOPES = ("openid", "profile", "email")
"""What this project needs to know who somebody is. Universal, and enough on its own."""

# Domains rather than whole host names, because every tenant has an issuer of its own
# beneath them.
PICTURE_SCOPES = (
    ("microsoftonline.com", "User.Read"),  # Entra ID, worldwide
    ("microsoftonline.us", "User.Read"),  # Entra ID, US government
    ("microsoftonline.cn", "User.Read"),  # Entra ID, China (21Vianet)
)
"""The extra permission a provider wants before handing over a profile picture, keyed by
the domain its issuer sits beneath."""


def scopes_for(issuer, override=""):
    """The scopes to ask an issuer for.

    Args:
        issuer: The configured issuer URL, whose host picks the picture scope.
        override: Space-separated scopes to ask for instead. There for the tenant that
            will not grant the extra permission, whose sign-in then fails outright
            rather than merely losing the picture.

    Returns:
        The scope names to send with the authorization request.
    """
    if named := override.split():
        return named

    host = (urlparse(issuer).hostname or "").lower()
    return [
        *STANDARD_SCOPES,
        # A dot before the domain, so only a host truly beneath it matches and not
        # merely one whose name ends in the same letters.
        *(
            scope
            for domain, scope in PICTURE_SCOPES
            if host == domain or host.endswith(f".{domain}")
        ),
    ]
