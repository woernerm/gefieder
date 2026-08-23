"""The fragment endpoint behind each panel placeholder."""

import json

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from . import charts
from .models import Panel
from .query import PanelQueryError, run


@staff_member_required
def panel_data(request, slug):
    """Run one panel's query and render it as a chart fragment.

    One request per panel, so a page holding several of them fetches all at once and a
    slow query delays only its own card.

    Args:
        request: The admin request; the user needs the panel view permission.
        slug: Which panel to render.

    Returns:
        The rendered fragment, carrying either the chart or the reason there is none.

    Raises:
        PermissionDenied: The signed-in user may not view panels.
    """
    if not request.user.has_perm("panels.view_panel"):
        raise PermissionDenied

    panel = get_object_or_404(Panel, slug=slug)

    # Only the panel's own declared defaults are bound. Reading them from the query
    # string instead would let anyone who can follow a link choose what a stored
    # statement is executed with.
    #
    # A failed query and a mistyped column name both belong in the card rather than in a
    # server error: HTMX swaps the fragment in, so one broken panel leaves the page
    # around it intact.
    try:
        columns, rows = run(panel.sql, panel.parameters)

        # ECharts has no table, so the one shape it cannot draw is asked for by name.
        if panel.options.get(charts.TABLE_KEY):
            return render(
                request,
                "panels/table.html",
                {"panel": panel, "columns": columns, "rows": rows},
            )

        options = json.dumps(charts.build(panel, columns, rows))
    except PanelQueryError as error:
        return render(request, "panels/error.html", {"panel": panel, "error": str(error)})

    return render(
        request,
        "panels/chart.html",
        {"panel": panel, "options": options, "empty": not rows},
    )
