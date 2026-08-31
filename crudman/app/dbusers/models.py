from django.contrib.auth.models import User
from django.db import models


class DatabaseUser(models.Model):
    """A person's own PostgreSQL login role.

    Unlike ``Tenant``, a real table rather than a cache over the catalog: only this row
    records which Django account a role belongs to. The credential is deliberately absent,
    so a lost password is re-provisioned rather than recovered.
    """

    # The Django account decides who exists and what rank they hold; deleting it takes
    # the row with it, and the pre_delete receiver disables the role.
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="database_user",
        verbose_name="administrator",
    )

    # Stored rather than derived (utils.role_name_for) so a later username change leaves
    # the role it created findable.
    role_name = models.CharField(
        "database role",
        max_length=63,
        unique=True,
        editable=False,
        help_text="The PostgreSQL login role, derived from the username.",
    )

    # Mirrored so the admin can show the rank without querying the catalog; the database
    # stays authoritative and re-provisioning rewrites both.
    group_role = models.CharField(
        "rank",
        max_length=32,
        editable=False,
        help_text="The gf_* group role carrying this user's privileges.",
    )

    # delete_db_user clears this rather than dropping the role, so what the person
    # created keeps its owner.
    is_enabled = models.BooleanField(
        "enabled",
        default=True,
        editable=False,
        help_text="Disabled roles keep everything they own but cannot connect.",
    )

    # An administrator enrolls someone, but the credential is generated on that person's
    # next sign-in and shown to them alone. Until then the role cannot connect.
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
