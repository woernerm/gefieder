from django import template

from ..stages import stage_of

register = template.Library()


@register.filter
def for_stage(app_list, path):
    """The apps of the stage the page belongs to, or every app outside any stage.

    Args:
        app_list: The admin's app list, as its each_context supplies it.
        path: The page's path.
    """
    stage = stage_of(path)
    if stage is None or not stage.apps:
        return app_list
    return [app for app in app_list if app["app_label"] in stage.apps]
