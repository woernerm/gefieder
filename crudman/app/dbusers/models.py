from django.contrib.auth.models import User
from django.db import models


class DatabaseUser(models.Model):
    """A person's own PostgreSQL login role.

    Only this row records which Django account a role belongs to. The credential is
    deliberately absent, so a lost password is re-provisioned rather than recovered.
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

    # What a developer's checkout authenticates with to fetch a password of its own. A
    # credential of this system's making rather than their sign-in: it does one thing --
    # mint a short-lived database password for its owner -- so a laptop never stores the
    # account that administers the system. Cleared when database access is switched off,
    # which is what revoking it means.
    token = models.CharField(
        "access token",
        max_length=64,
        blank=True,
        default="",
        editable=False,
        help_text="Authenticates a developer's checkout when it fetches a password.",
    )

    provisioned_on = models.DateTimeField("provisioned", auto_now=True)

    class Meta:
        verbose_name = "database user"
        verbose_name_plural = "database users"
        ordering = ("role_name",)

    def __str__(self):
        return self.role_name


class AccessToken(DatabaseUser):
    """The token page, as a model so the admin lists it under Access.

    A proxy of DatabaseUser rather than a table of its own: the token is a field on that
    row, and this exists only to give the sidebar an entry. Its changelist is replaced by
    the page a person creates their own token on.
    """

    class Meta:
        proxy = True
        # Beside the users and groups the section is about, rather than a heading of its
        # own for a single page.
        app_label = "auth"
        verbose_name = "access token"
        verbose_name_plural = "Access token"
