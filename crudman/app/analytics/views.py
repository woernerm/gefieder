"""The fragment endpoint behind each panel placeholder."""

import json

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from . import charts
from .charts import ChartBuildError
from .models import Panel
from .parameters import ParameterError
from .query import PanelQueryError, run_shared


@staff_member_required
def panel_data(request, slug):
    """Run one panel's query and render it as a chart fragment.

    One request per panel, so a page holding several of them fetches all at once and a
    slow query delays only its own card. Two panels built on the same query with the same
    values share one execution between them; see ``analytics.query.run_shared``.

    Args:
        request: The admin request; the user needs the panel view permission.
        slug: Which panel to render.

    Returns:
        The rendered fragment, carrying either the chart or the reason there is none.

    Raises:
        PermissionDenied: The signed-in user may not view panels.
    """
    if not request.user.has_perm("analytics.view_panel"):
        raise PermissionDenied

    panel = get_object_or_404(
        Panel.objects.select_related("query", "chart"), slug=slug
    )

    # Only the panel's own stored values are used. Reading them from the query string
    # instead would let anyone who can follow a link choose what a stored statement is
    # executed with -- which matters more now that a ${name:format} placeholder puts its
    # value into the statement text rather than beside it.
    #
    # A failed query, a missing parameter and options that are not shaped like an option
    # object all belong in the card rather than in a server error: HTMX swaps the fragment
    # in, so one broken panel leaves the page around it intact.
    try:
        columns, rows = run_shared(panel.query.sql, panel.resolved_parameters)
        options = json.dumps(charts.build(panel, columns, rows))
    except (PanelQueryError, ParameterError, ChartBuildError) as error:
        return render(
            request, "analytics/error.html", {"panel": panel, "error": str(error)}
        )

    return render(
        request,
        "analytics/chart.html",
        {"panel": panel, "options": options, "empty": not rows},
    )
