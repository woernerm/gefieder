from django.conf import settings
from django.db import models


class Tenant(models.Model):
    """A tenant of the analytics platform.

    PostgreSQL is the source of truth: a tenant is a role owning a ``bronze_<name>``
    schema, created by the ``create_tenant`` function. This table is only a cache, so the
    changelist has a real queryset to search, sort and paginate; every change goes through
    the database functions rather than ``save()`` / ``delete()``.
    """

    # The role and schema name doubles as the primary key, so the admin can build
    # per-object URLs without a synthetic id column.
    name = models.CharField(
        "slug",
        max_length=50,
        primary_key=True,
        help_text="Identifier used for the database role and bronze schema, e.g. project_a.",
    )

    # Tenants created outside crudman carry none, and sync_tenants falls back to the
    # slug.
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

    The limit fields default to it, so a freshly opened add form already means "no limit",
    and so does a blank field.
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


class Deployment(models.Model):
    """One attempt to put a commit of the models repository into production.

    A row per attempt rather than per commit: the same commit is deployed again when
    someone pins it back, and a refused attempt is worth keeping for the reason it carries.
    The newest row is the current deployment.

    The commit's own metadata is deliberately absent. It is in the repository, which the
    versions page reads anyway, and a copy here would be a second version of the truth.
    """

    PENDING, SUCCEEDED, FAILED = "pending", "succeeded", "failed"
    STATUSES = (
        (PENDING, "Applying"),
        (SUCCEEDED, "Live"),
        (FAILED, "Failed"),
    )

    sha = models.CharField("commit", max_length=40, editable=False)

    # What "main" pointed at when this deployment was made. The poll deploys only when the
    # branch has moved away from this, which is what lets a pinned older commit stand until
    # someone pushes -- and makes that push win, without any pin to clear.
    main_sha = models.CharField(max_length=40, editable=False)

    pinned = models.BooleanField(
        default=False,
        editable=False,
        help_text="A person chose this commit; the poll did not.",
    )

    status = models.CharField(max_length=16, choices=STATUSES, default=PENDING)

    # The engine's own words: the tail of a failed plan, or why the deployment was refused
    # before anything was checked out.
    message = models.TextField(blank=True, default="")

    # The model documentation the engine exported from this commit, in the shape
    # sqlmesh/docs_export.py writes. It lives with the deployment rather than in the image
    # because the models no longer ship in one, so the docs pages describe what is running.
    docs = models.JSONField(default=dict, blank=True, editable=False)

    # Null for the poll, which acts for nobody.
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        editable=False,
    )

    created_on = models.DateTimeField(auto_now_add=True)
    applied_on = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        # Named for what the admin shows: the versions of the models this system runs.
        verbose_name = "model version"
        verbose_name_plural = "model versions"
        ordering = ("-created_on",)
        get_latest_by = "created_on"

    def __str__(self):
        return f"{self.sha[:8]} ({self.get_status_display()})"

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def is_applying(self) -> bool:
        """Whether the engine has yet to report on this deployment.

        What the page spins a marker for, and what makes it reload: the row is closed by
        the engine in another container, so nothing here knows when that happens.
        """
        return self.status == self.PENDING
