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

    CHECKING_OUT = "checking_out"
    TRANSFORMING = "transforming"
    DOCUMENTING = "documenting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

    STATUSES = (
        (CHECKING_OUT, "Checking out"),
        (TRANSFORMING, "Transforming"),
        (DOCUMENTING, "Generating docs"),
        (SUCCEEDED, "Live"),
        (FAILED, "Failed"),
    )
    """The steps a deployment passes through, named for the one it is on.

    Three of them, because the wait is three pieces of work in two containers and a person
    watching deserves to know which one is taking the time: crudman puts the commit in the
    working tree, then the engine transforms the data, then it describes what it built.
    """

    FINISHED = (SUCCEEDED, FAILED)
    """The statuses nothing follows. Everything else is a step still running."""

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

    status = models.CharField(max_length=16, choices=STATUSES, default=CHECKING_OUT)

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

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def is_applying(self) -> bool:
        """Whether a step is still running.

        What the page spins a marker for, and what makes it reload: the row is advanced by
        the engine in another container, so nothing here knows when that happens.
        """
        return self.status not in self.FINISHED

    def __str__(self):
        return f"{self.sha[:8]} ({self.get_status_display()})"
