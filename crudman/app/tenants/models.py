from django.db import models


class Tenant(models.Model):
    """A tenant of the analytics platform.

    PostgreSQL is the source of truth: each tenant is a role owning a ``bronze_<name>``
    schema, created by the ``create_tenant`` database function. This table is only a
    cache the admin keeps in sync with those schemas, so the changelist has a real
    queryset to search, sort and paginate. Creating, editing and deleting a tenant goes
    through the database functions, not ``save()`` / ``delete()``.
    """

    # The role/schema name doubles as the primary key so the admin can build per-object
    # URLs without a synthetic id column. TenantCreationForm derives it from the display
    # name, so the user never has to know the slugging rules.
    name = models.CharField(
        "slug",
        max_length=50,
        primary_key=True,
        help_text="Identifier used for the database role and bronze schema, e.g. project_a.",
    )

    # Tenants created outside crudman, such as the seeded examples, carry no display name
    # in the database, so sync_tenants falls back to the slug for those.
    display_name = models.CharField(
        "name",
        max_length=100,
        blank=True,
        help_text="e.g. Project A",
    )

    UNLIMITED_COUNT = -1
    """PostgreSQL's "no limit" sentinel for the connection count."""

    UNLIMITED_SIZE = "0"
    """PostgreSQL's "no limit" sentinel for the size and time limits.

    The limit fields default to these sentinels, so a freshly opened add form already
    means "no limit"; a blank field means the same.
    """

    connection_limit = models.IntegerField(
        "connection limit",
        null=True,
        blank=True,
        default=UNLIMITED_COUNT,
        help_text="Maximum number of simultaneous database connections. -1 means no limit.",
    )
    statement_timeout = models.CharField(
        "statement timeout",
        max_length=32,
        null=True,
        blank=True,
        default=UNLIMITED_SIZE,
        help_text="Maximum runtime of a single statement, e.g. 5min, 10s, 1h. 0 means no limit.",
    )
    work_mem = models.CharField(
        "work memory",
        max_length=32,
        null=True,
        blank=True,
        default=UNLIMITED_SIZE,
        help_text="Maximum memory per query operation, e.g. 256MB, 1GB. 0 means no limit.",
    )
    temp_file_limit = models.CharField(
        "temp file limit",
        max_length=32,
        null=True,
        blank=True,
        default=UNLIMITED_SIZE,
        help_text="Maximum size of a temporary file, e.g. 1GB. 0 means no limit.",
    )

    class Meta:
        verbose_name = "tenant"
        verbose_name_plural = "tenants"

    def __str__(self):
        return self.display_name or self.name
