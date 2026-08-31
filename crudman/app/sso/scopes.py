"""What a sign-in asks the provider for.

The three standard OpenID Connect scopes carry every claim this project reads, except the
profile picture: some providers keep it behind their own API and want a permission of
their own. Which permission cannot be discovered — Entra ID's document advertises neither
User.Read nor the ``picture`` claim — so the provider is recognised by its issuer instead.
Asking everywhere would cost the sign-in, a provider refusing an authorization request
naming a scope it has never heard of.
"""
from urllib.parse import urlparse

STANDARD_SCOPES = ("openid", "profile", "email")
"""What this project needs to know who somebody is. Universal, and enough on its own."""

# Domains rather than host names: every tenant has an issuer of its own beneath them.
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
        override: Space-separated scopes to ask for instead, for the tenant that will
            not grant the extra permission.

    Returns:
        The scope names to send with the authorization request.
    """
    if named := override.split():
        return named

    host = (urlparse(issuer).hostname or "").lower()
    return [
        *STANDARD_SCOPES,
        # A dot before the domain, so a lookalike name ending in the same letters does
        # not match.
        *(
            scope
            for domain, scope in PICTURE_SCOPES
            if host == domain or host.endswith(f".{domain}")
        ),
    ]
