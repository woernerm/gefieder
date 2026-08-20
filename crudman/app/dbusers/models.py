from django.contrib.auth.models import User
from django.db import models


class DatabaseUser(models.Model):
    """A person's own PostgreSQL login role.

    Unlike ``Tenant``, this is a real table rather than a cache over the catalog. The
    catalog knows a role exists, but not which Django account it belongs to, and that link
    is the whole point: it is what lets a rank change in the identity provider reach the
    database, and what makes a query traceable back to a person.

    The credential itself is deliberately absent. PostgreSQL stores only a SCRAM verifier
    and crudman stores nothing, so a password that is lost is reset (re-provisioned), never
    recovered -- see dbusers/backends.py.
    """

    # One database role per Django user. The Django account is the source of truth for who
    # exists and what rank they hold; deleting it takes the row with it, and the signal in
    # apps.py disables the role at the same time.
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="database_user",
        verbose_name="administrator",
    )

    # The PostgreSQL role name. Derived from the username rather than typed (see
    # utils.role_name_for), but stored because the username may later change and the role
    # it created must still be findable.
    role_name = models.CharField(
        "database role",
        max_length=63,
        unique=True,
        editable=False,
        help_text="The PostgreSQL login role, derived from the username.",
    )

    # Mirrors the gf_* group role the person is a member of. Kept here so the admin can
    # show the rank without querying the catalog on every page load; the database remains
    # authoritative, and re-provisioning rewrites both.
    group_role = models.CharField(
        "rank",
        max_length=32,
        editable=False,
        help_text="The gf_* group role carrying this user's privileges.",
    )

    # Whether the role may currently log in. Set to False by delete_db_user rather than
    # dropping the role, so objects the person created keep their owner and the audit
    # trail survives their departure.
    is_enabled = models.BooleanField(
        "enabled",
        default=True,
        editable=False,
        help_text="Disabled roles keep everything they own but cannot connect.",
    )

    # Whether the role is still waiting for its password.
    #
    # An administrator enrolls someone, but the credential is generated on that person's
    # next sign-in and shown to them alone -- so the administrator never learns a password
    # that is not theirs, and nothing has to be stored in the meantime. Until then the role
    # exists with no password and cannot connect.
    awaiting_credential = models.BooleanField(
        "awaiting credential",
        default=True,
        editable=False,
        help_text="The password is issued on the user's next sign-in, once, to them.",
    )

    provisioned_on = models.DateTimeField("provisioned", auto_now=True)

    class Meta:
        verbose_name = "database user"
        verbose_name_plural = "database users"
        ordering = ("role_name",)

    def __str__(self):
        return self.role_name
