"""The Unfold components that place a panel, and a dashboard's grid, on a page.

Each renders a placeholder only; the data arrives afterwards over HTMX from
``analytics.views.panel_data``. That is what lets several panels on one page load at the
same time rather than one query after another, and it keeps a slow query from holding up
the page it sits on.
"""

from unfold.components import BaseComponent, register_component

from .models import Dashboard, Panel


@register_component
class PanelComponent(BaseComponent):
    """Looks a panel up by primary key and hands the template what it needs."""

    def get_context_data(self, **kwargs):
        """Resolve the ``panel`` primary key the template passed.

        Args:
            **kwargs: The component's template arguments; ``panel`` is the primary key.

        Returns:
            The context with the Panel added, or with it left None when no panel of that
            id exists -- an embedded panel that has been deleted leaves a note on the
            page rather than breaking the page it sits on.
        """
        context = super().get_context_data(**kwargs)
        panel = kwargs.get("panel")
        context["panel"] = (
            Panel.objects.select_related("query", "chart").filter(pk=panel).first()
            if str(panel or "").isdigit()
            else None
        )
        return context


@register_component
class DashboardComponent(BaseComponent):
    """Lays a dashboard's panels out across its grid.

    Placement is by flow: each panel states how many of the grid's columns it spans and
    where it comes in the order, and rows pack themselves. There is deliberately no way
    to name a row and a column, so a grid whose cells do not line up cannot be described.
    """

    def get_context_data(self, **kwargs):
        """Resolve the ``dashboard`` name the template passed.

        Args:
            **kwargs: The component's template arguments; ``dashboard`` names it.

        Returns:
            The context with the dashboard, its panels in order, the id its style block
            is written against, and the spans that block needs a rule for.
        """
        context = super().get_context_data(**kwargs)
        dashboard = Dashboard.objects.filter(name=kwargs.get("dashboard")).first()
        context["dashboard"] = dashboard
        panels = (
            list(dashboard.panels.select_related("query", "chart").order_by("order", "title"))
            if dashboard
            else []
        )
        context["panels"] = panels

        # Scoped to this grid so two dashboards on one page cannot restyle each other,
        # and only the spans in use get a rule.
        context["grid_id"] = f"gf-dashboard-{dashboard.name}" if dashboard else ""
        context["spans"] = sorted({panel.span for panel in panels})
        return context
