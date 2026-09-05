from django.conf import settings
from django.db import models


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
        ordering = ("-created_on",)
        get_latest_by = "created_on"

    def __str__(self):
        return f"{self.sha[:8]} ({self.get_status_display()})"

    @property
    def short_sha(self) -> str:
        return self.sha[:8]
