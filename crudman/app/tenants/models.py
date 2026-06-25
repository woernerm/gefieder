from django.db import models


class Tenant(models.Model):
    """A tenant of the analytics platform.

    The source of truth for tenants is PostgreSQL: each tenant is a role that owns a
    ``<name>_bronze`` schema, created by the ``create_tenant`` database function. This
    table is only a cache that the admin keeps in sync with those schemas (see
    ``tenants.utils.get_tenants`` and ``TenantAdmin.get_queryset``) so the standard admin
    changelist — with its searching, sorting and pagination — has a real queryset to work
    with. Creating, editing and deleting a tenant goes through the database functions, not
    ``save()`` / ``delete()``.
    """

    # The role/schema name doubles as the primary key so the admin can build per-object
    # URLs without a synthetic id column.
    name = models.CharField(
        "name",
        max_length=50,
        primary_key=True,
        help_text="e.g. max_mustermann or customer_a_project",
    )

    # All limit fields default to None, meaning no limit (infinite). They map to the
    # arguments of the set_tenant_limits database function.
    connection_limit = models.IntegerField(
        "connection limit",
        null=True,
        blank=True,
        help_text="Maximum number of simultaneous database connections.",
    )
    statement_timeout = models.CharField(
        "statement timeout",
        max_length=32,
        null=True,
        blank=True,
        help_text="Maximum runtime of a single statement, e.g. 5min, 10s, 1h.",
    )
    work_mem = models.CharField(
        "work memory",
        max_length=32,
        null=True,
        blank=True,
        help_text="Maximum memory per query operation, e.g. 256MB, 1GB.",
    )
    temp_file_limit = models.CharField(
        "temp file limit",
        max_length=32,
        null=True,
        blank=True,
        help_text="Maximum size of a temporary file, e.g. 1GB.",
    )

    class Meta:
        verbose_name = "tenant"
        verbose_name_plural = "tenants"

    def __str__(self):
        return self.name
