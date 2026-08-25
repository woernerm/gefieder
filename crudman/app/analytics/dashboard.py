"""What the admin dashboard shows above its app list.

Unfold hands the dashboard template whatever this adds to the context; it is named by
UNFOLD["DASHBOARD_CALLBACK"] in settings.py.
"""

HOME_NAME = "home"
"""The dashboard placed on the admin index.

A name rather than a flag on the model: the admin index is one page, so "which dashboard
is it" has exactly one answer, and a reserved name says that without a field that has to
be kept unique.
"""


def dashboard_callback(request, context):
    """Name the dashboard the admin index should carry.

    Args:
        request: The dashboard request; the dashboard is named only for a user who may
            see panels, so the placeholders are not even rendered for anyone else.
        context: The template context to extend.

    Returns:
        The context, with the dashboard name added.
    """
    context["dashboard_name"] = (
        HOME_NAME if request.user.has_perm("analytics.view_panel") else None
    )
    return context
