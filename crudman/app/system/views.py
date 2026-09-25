"""Where a stakeholder answers about the version they were asked to review.

Not an admin page: the people asked hold the viewer rank and their business is the data,
not the machinery. What lets them answer is the undecided row itself -- nobody can decide
about a version they were not asked about, so the row is the permission.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from .models import Approval


@login_required
@require_POST
def decide(request):
    """Record this person's decision and put them back on production.

    Back on production because the answer is what ends their review: the next page they
    ask for is answered without the preview data source, which is the whole of leaving.
    """
    approval = Approval.owed_by(request.user)
    if approval is None:
        raise Http404

    approval.decide(
        approved=request.POST.get("decision") == "approve",
        comment=request.POST.get("comment", "").strip(),
    )
    return redirect("/")
