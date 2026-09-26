from django.conf import settings
from django.db import models
from django.utils import timezone


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

    PROD = "prod"
    PREVIEW = "preview"
    """The environments a commit is deployed into.

    ``prod`` is what the dashboards read and the only one the poll makes. ``preview`` is a
    version under review: SQLMesh plans it into an environment of its own, so only what the
    commit changes is built and everything else stays the production table. The name is a
    field rather than a flag because a second review is a second environment and nothing
    else -- see the requirements.
    """

    sha = models.CharField("commit", max_length=40, editable=False)

    environment = models.CharField(max_length=40, default=PROD, editable=False)

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
        # Named for what the admin shows under its "Models" heading: the versions.
        verbose_name = "version"
        verbose_name_plural = "versions"
        ordering = ("-created_on",)
        get_latest_by = "created_on"

    @classmethod
    def current(cls, environment: str = PROD):
        """The newest deployment of an environment, which is the one it is running."""
        return cls.objects.filter(environment=environment).first()

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def is_approved(self) -> bool:
        """Whether every stakeholder asked has approved this version.

        A review with nobody assigned is not approved: it was never put to anyone.
        """
        decisions = list(self.approvals.values_list("approved", flat=True))
        return bool(decisions) and all(decisions)

    @property
    def is_applying(self) -> bool:
        """Whether a step is still running.

        What the page spins a marker for, and what makes it reload: the row is advanced by
        the engine in another container, so nothing here knows when that happens.
        """
        return self.status not in self.FINISHED

    def __str__(self):
        return f"{self.sha[:8]} ({self.get_status_display()})"


class Approval(models.Model):
    """One stakeholder's decision about the version under review.

    A row per person asked, created when the review starts and left undecided until they
    answer -- so "who still owes a decision" is a query rather than a second table, and it
    is also what puts them on the preview: crudman tells the proxy to show a person the
    reviewed version exactly while an undecided row of theirs exists (notebooks/views.py).

    The person is any account that has signed in, not a rank and not a group. Who should
    judge a metric depends on the metric, and a standing group would answer a different
    question.
    """

    deployment = models.ForeignKey(
        Deployment, on_delete=models.CASCADE, related_name="approvals"
    )

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    # Undecided until they answer, which is what makes them a reviewer rather than a
    # record of one.
    approved = models.BooleanField(null=True, blank=True)

    comment = models.TextField(blank=True, default="")

    decided_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        # One decision per person per review: asking someone twice is the same request.
        constraints = [
            models.UniqueConstraint(
                fields=("deployment", "user"), name="one_approval_per_reviewer"
            )
        ]

    @classmethod
    def owed_by(cls, user):
        """The decision this person still owes, or None.

        The newest review only: an older one they never answered is superseded, and
        showing them a version nobody is waiting on would strand them off production.

        One query rather than two, the newest review being a subquery rather than a
        lookup: this is asked on every page the bar is drawn on and for every panel a
        dashboard draws, which reads the version its viewer owes a decision on.
        """
        newest_review = Deployment.objects.filter(
            environment=Deployment.PREVIEW
        ).values("pk")[:1]
        return cls.objects.filter(
            user=user, approved__isnull=True, deployment__in=newest_review
        ).first()

    def decide(self, approved: bool, comment: str = "") -> None:
        """Record what this person decided."""
        self.approved = approved
        self.comment = comment
        self.decided_on = timezone.now()
        self.save(update_fields=["approved", "comment", "decided_on"])

    def __str__(self):
        return f"{self.user} on {self.deployment.short_sha}"
