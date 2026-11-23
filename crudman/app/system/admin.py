from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.core.exceptions import PermissionDenied
from django.urls import path
from unfold.admin import ModelAdmin

from . import repo
from .models import Deployment

CHANGELIST = "admin:system_deployment_changelist"
NOT_ON_BRANCH = "That commit is not on the branch and was not deployed."


@admin.register(Deployment)
class DeploymentAdmin(ModelAdmin):
    """The models repository's history, and which commit production is running.

    Under System, as choosing what the engine computes is administration. The changelist is
    replaced: the page answers "which commit shall run", and the commits come from git
    rather than from this table.
    """

    def _require_rank(self, request):
        """What both buttons take before they change anything.

        admin_view has already required a signed-in staff account; this is the rank.
        django.views.decorators.http.require_POST would take ``self`` as the request.

        Raises:
            PermissionDenied: Not a POST, or too low a rank.
        """
        if request.method != "POST" or not request.user.has_perm("system.add_deployment"):
            raise PermissionDenied

    def _refuse(self, request, reason):
        """Back to the versions page, saying why nothing was done."""
        messages.error(request, reason)
        return redirect(CHANGELIST)

    def _apply(self, request, action, failure, success):
        """Run what a button asked for and report how it went on the versions page.

        Args:
            request: The POST the button sent.
            action: Returns the Deployment it recorded, or raises repo.GitError.
            failure: What could not be done to the commit, for a git error.
            success: The message for a deployment that did not fail; ``{sha}`` is
                replaced by its short sha.
        """
        try:
            deployment = action()
        except repo.GitError as error:
            return self._refuse(request, f"The commit could not be {failure}: {error}")
        if deployment.status == Deployment.FAILED:
            return self._refuse(request, deployment.message)
        messages.success(request, success.format(sha=deployment.short_sha))
        return redirect(CHANGELIST)

    # A deployment is made by pressing the button below, and an existing one is a record of
    # something that already happened. So there is nothing to add, edit or delete by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        """Readable by whoever may change what runs, and by nobody else.

        Every rank is staff, so the admin's own check would let a viewer in here.
        """
        return request.user.has_perm("system.add_deployment")

    def get_urls(self):
        # Before the base URLs, whose "<path:object_id>/" would otherwise swallow this.
        return [
            path(
                "deploy/",
                self.admin_site.admin_view(self.deploy),
                name="system_deployment_deploy",
            ),
            path(
                "review/",
                self.admin_site.admin_view(self.review),
                name="system_deployment_review",
            ),
            *super().get_urls(),
        ]

    def changelist_view(self, request, extra_context=None):
        # The base implementation would do this; replacing it wholesale means doing it
        # here, and it is the whole access rule for the page.
        if not self.has_view_permission(request):
            raise PermissionDenied

        current = Deployment.current()
        review = Deployment.current(Deployment.PREVIEW)

        commits = repo.log()
        for commit in commits:
            # The review of this commit, so a row can say whether it has been through one
            # and how it went. Only the newest review counts, which is the one the page
            # would otherwise have to work out.
            commit["review"] = review if review and review.sha == commit["sha"] else None

        context = {
            **self.admin_site.each_context(request),
            "title": "Versions",
            "commits": commits,
            "current": current,
            "current_sha": current.sha if current else None,
            "review": review,
            # Anyone who has signed in at least once, because that is the whole directory
            # this system has and who should judge a metric is not a rank.
            "stakeholders": User.objects.filter(
                is_active=True, last_login__isnull=False
            ).order_by("first_name", "username"),
            "clone_url": repo.clone_url(),
            "project": repo.PROJECT,
            **(extra_context or {}),
        }
        return TemplateResponse(request, "system/versions.html", context)

    def review(self, request):
        """Put a commit up for review and ask the chosen people to decide about it.

        Starting another review supersedes the one before, so a stakeholder signs in and sees
        the one version they were asked about.
        """
        self._require_rank(request)
        sha = request.POST.get("sha", "").strip()
        stakeholders = User.objects.filter(pk__in=request.POST.getlist("approvers"))

        if not repo.is_on_branch(sha):
            return self._refuse(request, NOT_ON_BRANCH)
        if not stakeholders:
            return self._refuse(request, "Name at least one person to approve this version.")
        return self._apply(
            request,
            lambda: repo.review(sha, stakeholders, user=request.user),
            "previewed",
            f"Version {{sha}} is being prepared for review. {len(stakeholders)} person(s) "
            "will see it in place of production the next time they sign in.",
        )

    def deploy(self, request):
        """Put the chosen commit into production.

        A pin, so the poll leaves it alone until someone pushes; that push then supersedes
        it, which is why nothing here has to be un-pinned later.
        """
        self._require_rank(request)
        sha = request.POST.get("sha", "").strip()

        if not repo.is_on_branch(sha):
            return self._refuse(request, NOT_ON_BRANCH)
        # A version that was put up for review goes live when the review says so. One that
        # never was is deployed as it always has been -- the page asks first, and the poll
        # keeps deploying what is pushed, so review is a control an operator reaches for
        # rather than a gate across the whole system.
        review = Deployment.objects.filter(
            environment=Deployment.PREVIEW, sha=sha
        ).first()
        if review and not review.is_approved:
            return self._refuse(
                request,
                f"Version {review.short_sha} is under review and not everyone has "
                "approved it yet.",
            )
        return self._apply(
            request,
            lambda: repo.deploy(sha, pinned=True, user=request.user),
            "deployed",
            "Version {sha} is being applied. The engine picks it up within a few seconds; "
            "this page shows the result.",
        )
