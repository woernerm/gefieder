"""What every page needs to know about how the system is configured to look."""
import os


def default_theme(request):
    """The colour mode a browser starts in, from DEFAULT_THEME in runtime.env.

    Read by the skeleton template, which hands it to Unfold's theme switcher as the
    starting value. Only ever "dark" or "light": anything else in the file falls back to
    dark, as it does in Grafana and the hub.

    Args:
        request: The request being rendered, unused.

    Returns:
        The mode under "default_theme".
    """
    return {"default_theme": "light" if os.environ.get("DEFAULT_THEME") == "light" else "dark"}
