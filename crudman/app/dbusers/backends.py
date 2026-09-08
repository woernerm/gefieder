"""How a database user proves who they are — the one part meant to be replaced.

Provisioning a role and deciding how it authenticates change on different timescales, so
they are kept apart. Today the answer is a password (scram-sha-256), the only method that
works end to end; it is never a standing one, being issued with an expiry to whoever is
about to use it (``issue_password`` in utils).

PostgreSQL 18's OAuth 2.0 bearer tokens would let a person connect with their provider
identity instead. The server ships the OAuth framework without a validator module, and the
device-authorization flow libpq implements wants a human at a browser rather than a
notebook kernel, so an OAuthBackend joins ScramBackend when both are resolved and the
provisioning code does not change. See dbusers/requirements.md.
"""
import secrets


class ProvisioningBackend:
    """What provisioning needs to know about an authentication method.

    Attributes:
        name: Shown in the admin so an operator can see which method is active.
    """

    name = ""

    def make_secret(self) -> str | None:
        """The password to issue, or None where the method needs none."""
        return None


class ScramBackend(ProvisioningBackend):
    """A password of this system's own making, verified by PostgreSQL (scram-sha-256).

    Not the person's single sign-on password: no PostgreSQL authentication method
    available here can check one against the provider. A database-only credential, issued
    with an expiry to the client that is about to connect and never stored.
    """

    name = "Password (scram-sha-256)"

    SECRET_BYTES = 32
    """Bytes of URL-safe randomness per password, well above the functions' minimum."""

    def make_secret(self) -> str:
        return secrets.token_urlsafe(self.SECRET_BYTES)


def get_backend() -> ProvisioningBackend:
    """The authentication method in force.

    Returns:
        The backend. A function, so the choice can later follow a setting.
    """
    return ScramBackend()
