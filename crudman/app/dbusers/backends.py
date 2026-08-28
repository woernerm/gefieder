"""How a database user proves who they are — the one part meant to be replaced.

Provisioning a role and deciding how it authenticates change on different timescales, so
they are kept apart. Today the answer is a password (scram-sha-256), the only method that
works end to end. PostgreSQL 18's OAuth 2.0 bearer tokens would let an administrator
connect with their Entra ID identity instead, but two things are missing:

  * PostgreSQL 18 ships the OAuth framework without a validator module; the server needs
    a third-party library in oauth_validator_libraries to check the token.
  * psycopg2-binary bundles its own libpq, currently 17, which predates the feature. Entra
    ID has no LDAP endpoint either, so LDAP is no shortcut around this.

When both are resolved an OAuthBackend joins ScramBackend and the provisioning code does
not change. See dbusers/requirements.md.
"""
import secrets

from django.conf import settings


class ProvisioningBackend:
    """What provisioning needs to know about an authentication method.

    A backend answers what credential, if any, the database role carries, and provides
    the instructions that go with it. Everything else about a database user is the same
    whichever method is in use.

    Attributes:
        name: Shown in the admin so an operator can see which method is active.
        issues_secret: Whether provisioning produces a secret the person has to be told.
            False where the identity provider holds the credential, which makes the
            admin's "copy this password" step disappear rather than show an empty box.
    """

    name = ""
    issues_secret = False

    def make_secret(self) -> str | None:
        """The password to create the role with, or None when the method needs none."""
        return None

    def connection_hint(self, user_name: str) -> str:
        """How to connect, in one line, for the admin page.

        Args:
            user_name: The person's database role name.

        Returns:
            The instructions to show them.
        """
        return f"Connect as the database user {user_name}."


class ScramBackend(ProvisioningBackend):
    """A password of this system's own making, verified by PostgreSQL (scram-sha-256).

    Not the person's single sign-on password and it cannot be: Entra ID never discloses
    it, and no PostgreSQL authentication method available here can check it against the
    provider. A separate, database-only credential, shown once and never stored in a
    recoverable form.
    """

    name = "Password (scram-sha-256)"
    issues_secret = True

    SECRET_BYTES = 32
    """Bytes of URL-safe randomness per password.

    Comfortably above the 12-character minimum create_db_user enforces, and no one has to
    type it twice.
    """

    def make_secret(self) -> str:
        return secrets.token_urlsafe(self.SECRET_BYTES)

    def connection_hint(self, user_name: str) -> str:
        return (
            f"To run sqlmesh, set SQLMESH_PASSWORD to this password."
        )


def get_backend() -> ProvisioningBackend:
    """The authentication method in force.

    Returns:
        The backend. A function rather than a module-level constant so the choice can
        later follow a setting without every caller changing.
    """
    return ScramBackend()
