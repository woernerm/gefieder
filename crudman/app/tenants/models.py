from django.db import models


class Tenant(models.Model):
    """A tenant of the analytics platform.

    The source of truth for tenants is PostgreSQL: each tenant is a role that owns a
    ``bronze_<name>`` schema, created by the ``create_tenant`` database function. This
    table is only a cache that the admin keeps in sync with those schemas (see
    ``tenants.utils.get_tenants`` and ``TenantAdmin.get_queryset``) so the standard admin
    changelist — with its searching, sorting and pagination — has a real queryset to work
    with. Creating, editing and deleting a tenant goes through the database functions, not
    ``save()`` / ``delete()``.
    """

    # The role/schema name doubles as the primary key so the admin can build per-object
    # URLs without a synthetic id column. It is a snake_case slug because it has to be a
    # valid PostgreSQL identifier (the tenant's role and bronze_<name> schema); the human
    # name the user typed is kept separately in display_name. The slug is derived from the
    # display name on the add form (see TenantCreationForm), so the user never types it.
    name = models.CharField(
        "slug",
        max_length=50,
        primary_key=True,
        help_text="Identifier used for the database role and bronze schema, e.g. project_a.",
    )

    # The human-friendly name as entered in the admin, e.g. "Project A". It may contain
    # spaces and capitals; only display_name is shown in the changelist. Tenants created
    # outside crudman (e.g. the seeded example tenants) have no display name in the
    # database, so sync_tenants falls back to the slug for those.
    display_name = models.CharField(
        "name",
        max_length=100,
        blank=True,
        help_text="e.g. Project A",
    )

    # The limit fields map to the arguments of the set_tenant_limits database function.
    # Their defaults are PostgreSQL's "unlimited" sentinels, so a freshly opened add form
    # is pre-filled with values that mean "no limit": -1 for the connection count and 0
    # for the size/time limits. Leaving a field blank means the same (no limit); the
    # utility functions map both the sentinel and an empty value to/from None.
    UNLIMITED_COUNT = -1
    UNLIMITED_SIZE = "0"

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
