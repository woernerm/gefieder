"""How a database user proves who they are -- the one part meant to be replaced.

Provisioning a person's database role and deciding how that role authenticates are two
different questions, and they change on different timescales. Which schemas an editor may
write is a decision about this system; whether the role carries a password or presents a
token from the identity provider is a decision about what PostgreSQL and the drivers
support in a given year. Keeping them apart is what makes the second one cheap to revisit.

Today the answer is a password (scram-sha-256), because it is the only method that works
end to end. PostgreSQL gained OAuth 2.0 bearer-token authentication in version 18, which
would let an administrator connect with their Entra ID identity and no separate database
password at all -- but two things are missing:

  * PostgreSQL 18 ships the OAuth framework without a validator module; the server needs a
    third-party library in oauth_validator_libraries to check the token.
  * The clients here cannot use it. psycopg2-binary bundles its own libpq, currently 17,
    which predates the feature -- so SQLMesh could not connect that way even against an
    18 server. Entra ID has no LDAP endpoint either (that needs Entra Domain Services, a
    separate managed domain), so LDAP is not a shortcut around this.

When both are resolved, an OAuthBackend below joins ScramBackend and the provisioning code
does not change: a backend says whether it issues a secret, and the role is created with
or without a password accordingly. See dbusers/requirements.md.
"""
import secrets

from django.conf import settings


class ProvisioningBackend:
    """What provisioning needs to know about an authentication method.

    A backend answers one question -- what credential, if any, does the database role
    carry -- and provides the human-readable instructions that go with it. Everything
    else about a database user (its name, its rank, when it is disabled) is the same
    whichever method is in use.
    """

    #: Shown in the admin so an operator can see which method is active.
    name = ""

    #: Whether provisioning produces a secret the person has to be told. False for methods
    #: where the identity provider holds the credential, which is what makes the admin's
    #: "copy this password" step disappear rather than showing an empty box.
    issues_secret = False

    def make_secret(self) -> str | None:
        """The password to create the role with, or None when the method needs none."""
        return None

    def connection_hint(self, user_name: str) -> str:
        """How to connect, in one line, for the admin page."""
        return f"Connect as the database user {user_name}."


class ScramBackend(ProvisioningBackend):
    """A password of this system's own making, verified by PostgreSQL (scram-sha-256).

    The password is not the person's single sign-on password and cannot be: Entra ID never
    discloses it, and no PostgreSQL authentication method available here can check it
    against the provider. It is a separate, database-only credential -- generated here,
    shown once, and never stored in a recoverable form (PostgreSQL keeps only a SCRAM
    verifier, and crudman keeps nothing at all).
    """

    name = "Password (scram-sha-256)"
    issues_secret = True

    # 32 bytes of URL-safe randomness. Comfortably above the 12-character minimum the
    # create_db_user database function enforces, and no one has to type it twice.
    SECRET_BYTES = 32

    def make_secret(self) -> str:
        return secrets.token_urlsafe(self.SECRET_BYTES)

    def connection_hint(self, user_name: str) -> str:
        return (
            f"psql -h <server> -U {user_name} -d {settings.DATABASES['default']['NAME']}, "
            f"or set SQLMESH_PASSWORD to "
            f"this password to run sqlmesh with your own account."
        )


def get_backend() -> ProvisioningBackend:
    """The authentication method in force.

    A function rather than a module-level constant so the choice can later follow a
    setting without every caller changing.
    """
    return ScramBackend()
