"""What the admin dashboard shows above its app list.

Unfold hands the dashboard template whatever this adds to the context; it is named by
UNFOLD["DASHBOARD_CALLBACK"] in settings.py.
"""

from .models import Panel


def dashboard_callback(request, context):
    """Add the slugs of the panels the dashboard should carry.

    Args:
        request: The dashboard request; panels are listed only for a user who may see
            them, so the placeholders are not even rendered for anyone else.
        context: The template context to extend.

    Returns:
        The context, with the panel slugs added.
    """
    slugs = []
    if request.user.has_perm("panels.view_panel"):
        slugs = list(
            Panel.objects.filter(on_dashboard=True).values_list("slug", flat=True)
        )
    context["dashboard_panels"] = slugs
    return context
