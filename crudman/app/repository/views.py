"""The model versions page: what has been committed, and what is running.

Built on the documentation pages rather than beside them. The two answer one question
between them -- what does this system compute, and since when -- so they share their
chrome, their sidebar and the rank that reaches them.
"""

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from docs.views import DocsView

from . import repo
from .models import Deployment


class VersionsView(DocsView):
    """Every commit on the branch, with the one that is running marked."""

    template_name = "repository/versions.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current = Deployment.objects.first()
        context.update(
            {
                "title": "Model versions",
                "commits": repo.log(),
                "current": current,
                "current_sha": current.sha if current else None,
                "clone_url": repo.clone_url(),
                "project": repo.PROJECT,
                # The button changes what production computes, so it takes the rank that
                # may change things rather than the one that may read them.
                "can_deploy": self.request.user.has_perm("repository.add_deployment"),
            }
        )
        return context


@require_POST
@permission_required("repository.add_deployment")
def deploy(request):
    """Put the chosen commit into production.

    A pin, so the poll leaves it alone until someone pushes; that push then supersedes it,
    which is why nothing here has to be un-pinned later.
    """
    sha = request.POST.get("sha", "").strip()

    if not repo.is_on_branch(sha):
        messages.error(request, "That commit is not on the branch and was not deployed.")
        return redirect("repository:versions")

    try:
        deployment = repo.deploy(sha, pinned=True, user=request.user)
    except repo.GitError as error:
        messages.error(request, f"The commit could not be deployed: {error}")
        return redirect("repository:versions")

    if deployment.status == Deployment.FAILED:
        messages.error(request, deployment.message)
    else:
        messages.success(
            request,
            f"Version {deployment.short_sha} is being applied. The engine picks it up "
            f"within a few seconds; this page shows the result.",
        )
    return redirect("repository:versions")
