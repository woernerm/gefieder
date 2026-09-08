"""What the sidebar needs to know about the notebooks.

A context processor rather than an Unfold setting: `SIDEBAR["navigation"]` replaces the
app list with whatever it is given, hiding every registered model, and `SITE_DROPDOWN`
renders a panel that only opens once somebody clicks the site name. Neither is a link a
person finds. So the template is overridden instead (`templates/unfold/helpers/`), and
this is what tells it whether to draw the entry.
"""
import os

NOTEBOOK_PATH = os.environ.get("NOTEBOOK_PATH", "jupyter")
"""Where the proxy serves JupyterHub, from buildtime.env. crudman only links to it."""


def notebooks(request):
    """The address of the notebooks, for someone who may actually open them.

    Args:
        request: The request being rendered.

    Returns:
        The URL under "notebooks_url", or an empty context. Absent for anyone the hub
        would refuse, so the sidebar never offers a page that answers 403.
    """
    from .utils import may_use_notebooks

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not may_use_notebooks(user):
        return {}
    return {"notebooks_url": f"/{NOTEBOOK_PATH}/"}
