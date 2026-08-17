"""What a sign-in asks the provider for.

The three standard OpenID Connect scopes carry every claim this project reads, and every
provider has them. The profile picture is the exception: some providers keep it behind
their own API and want a permission of their own before parting with it.

Which permission cannot be discovered. Entra ID's own document advertises
`"scopes_supported": ["openid", "profile", "email", "offline_access"]` and stops there,
while the permission its photographs actually need -- User.Read, a Microsoft Graph
permission rather than an OpenID Connect scope -- goes unmentioned. Its `claims_supported`
omits `picture` as well, though its userinfo endpoint returns one. So the document cannot
be asked, and the provider is recognised by the issuer it was configured with instead.

Asking everywhere would be worse than useless: a provider refuses an authorization request
naming a scope it has never heard of, so a scope asked of the wrong provider does not cost
a picture, it costs the sign-in.
"""
from urllib.parse import urlparse

# What this project needs to know who somebody is. Universal, and enough on its own.
STANDARD_SCOPES = ("openid", "profile", "email")

# The extra permission a provider wants before it will hand over a profile picture, found
# by the host its issuer names. Written as domains rather than whole host names because
# every tenant has an issuer of its own beneath them.
PICTURE_SCOPES = (
    ("microsoftonline.com", "User.Read"),  # Entra ID, worldwide
    ("microsoftonline.us", "User.Read"),  # Entra ID, US government
    ("microsoftonline.cn", "User.Read"),  # Entra ID, China (21Vianet)
)


def scopes_for(issuer, override=""):
    """The scopes to ask `issuer` for, or the ones `override` names instead.

    The override is there for the tenant that will not grant the extra permission. Such a
    sign-in fails outright rather than merely losing the picture, and naming the standard
    scopes puts it back as it was without waiting for a new release.
    """
    if named := override.split():
        return named

    host = (urlparse(issuer).hostname or "").lower()
    return [
        *STANDARD_SCOPES,
        # A dot before the domain, so that only a host truly beneath it matches and not
        # merely one whose name ends in the same letters.
        *(
            scope
            for domain, scope in PICTURE_SCOPES
            if host == domain or host.endswith(f".{domain}")
        ),
    ]
