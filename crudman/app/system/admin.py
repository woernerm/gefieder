from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.core.exceptions import PermissionDenied
from django.urls import path, reverse
from unfold.admin import ModelAdmin

from . import repo
from .models import Deployment


@admin.register(Deployment)
class DeploymentAdmin(ModelAdmin):
    """The models repository's history, and which commit production is running.

    An admin page rather than one of its own: choosing what the engine computes is
    administration, so it belongs under System and behind the same sign-in, not on the
    documentation pages that are open from the viewer rank up.

    The changelist is replaced wholesale. A row here records a deployment, but the question
    the page answers is "which commit shall run", and the commits come from git rather than
    from this table.
    """

    def _require_rank(self, request):
        """What both buttons take before they change anything.

        Once rather than in each of them: two copies of a permission check are two
        chances to change only one. admin_view has already required a signed-in staff
        account; this is the rank.

        Not django.views.decorators.http.require_POST: these decorators take the request
        as their first argument, which on a method is self.

        Raises:
            PermissionDenied: Not a POST, or too low a rank.
        """
        if request.method != "POST" or not request.user.has_perm("system.add_deployment"):
            raise PermissionDenied

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

        Every rank is staff, so the admin's own check would let a viewer in here. The page
        exists to move production between versions, and a viewer's business is the data
        rather than the machinery, so it takes the rank that may change things.
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

        One review at a time: starting another supersedes the one before it, whose
        approvers are then no longer asked anything. That is what keeps the workflow
        wordless for a stakeholder -- they sign in and are looking at the thing they were
        asked about, rather than choosing between several.
        """
        self._require_rank(request)
        changelist = reverse("admin:system_deployment_changelist")

        sha = request.POST.get("sha", "").strip()
        stakeholders = User.objects.filter(pk__in=request.POST.getlist("approvers"))

        if not repo.is_on_branch(sha):
            messages.error(request, "That commit is not on the branch and was not deployed.")
            return redirect(changelist)

        if not stakeholders:
            messages.error(request, "Name at least one person to approve this version.")
            return redirect(changelist)

        try:
            deployment = repo.review(sha, stakeholders, user=request.user)
        except repo.GitError as error:
            messages.error(request, f"The commit could not be previewed: {error}")
            return redirect(changelist)

        if deployment.status == Deployment.FAILED:
            messages.error(request, deployment.message)
        else:
            messages.success(
                request,
                f"Version {deployment.short_sha} is being prepared for review. "
                f"{len(stakeholders)} person(s) will see it in place of production the "
                "next time they sign in.",
            )
        return redirect(changelist)

    def deploy(self, request):
        """Put the chosen commit into production.

        A pin, so the poll leaves it alone until someone pushes; that push then supersedes
        it, which is why nothing here has to be un-pinned later.
        """
        self._require_rank(request)
        changelist = reverse("admin:system_deployment_changelist")

        sha = request.POST.get("sha", "").strip()

        if not repo.is_on_branch(sha):
            messages.error(request, "That commit is not on the branch and was not deployed.")
            return redirect(changelist)

        # A version that was put up for review goes live when the review says so. One that
        # never was is deployed as it always has been -- the page asks first, and the poll
        # keeps deploying what is pushed, so review is a control an operator reaches for
        # rather than a gate across the whole system.
        review = Deployment.objects.filter(
            environment=Deployment.PREVIEW, sha=sha
        ).first()
        if review and not review.is_approved:
            messages.error(
                request,
                f"Version {review.short_sha} is under review and not everyone has "
                "approved it yet.",
            )
            return redirect(changelist)

        try:
            deployment = repo.deploy(sha, pinned=True, user=request.user)
        except repo.GitError as error:
            messages.error(request, f"The commit could not be deployed: {error}")
            return redirect(changelist)

        if deployment.status == Deployment.FAILED:
            messages.error(request, deployment.message)
        else:
            messages.success(
                request,
                f"Version {deployment.short_sha} is being applied. The engine picks it up "
                f"within a few seconds; this page shows the result.",
            )
        return redirect(changelist)
