"""The Unfold component that places a panel on a page.

The component renders a placeholder only; the data arrives afterwards over HTMX from
``panels.views.panel_data``. That is what lets several panels on one page load at the
same time rather than one query after another, and it keeps a slow query from holding up
the page it sits on.
"""

from unfold.components import BaseComponent, register_component

from .models import Panel


@register_component
class PanelComponent(BaseComponent):
    """Looks a panel up by slug and hands the template what the placeholder needs."""

    def get_context_data(self, **kwargs):
        """Resolve the ``panel`` slug the template passed.

        Args:
            **kwargs: The component's template arguments; ``panel`` names the slug.

        Returns:
            The context with the Panel added, or with it left None when no panel of that
            slug exists -- an embedded panel that has been deleted leaves a note on the
            page rather than breaking the page it sits on.
        """
        context = super().get_context_data(**kwargs)
        context["panel"] = Panel.objects.filter(slug=kwargs.get("panel")).first()
        return context
