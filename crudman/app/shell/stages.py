"""The stages of the workflow the bar offers, and which of them a person may enter.

Stages rather than apps: a person sets out to look at dashboards or to load data, not to
open the admin panel. Two of the four lead into the admin panel, each to a different part
of it, and the sidebar there shows only that part (templatetags/shell.py) -- so the panel
is not one entry in the bar but two small tools sharing a sign-in.
"""
from collections.abc import Callable
from dataclasses import dataclass

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import reverse

from dropzones.models import Dropzone
from notebooks.utils import may_use_notebooks
from system.models import Deployment


def may_change(user, apps):
    """Whether the person may do more than look, in one of the apps.

    A viewer holds view permissions on most of the panel, so "any permission" would light
    every stage for everyone. What tells a stage's users from its onlookers is the right to
    add, change or delete something there.

    Args:
        user: The Django user.
        apps: The app labels making up the stage.

    Returns:
        True if any permission the user holds in those apps is not a view permission.
    """
    return any(
        app in apps and not codename.startswith("view_")
        for app, _, codename in (perm.partition(".") for perm in user.get_all_permissions())
    )


@dataclass(frozen=True)
class Stage:
    label: str
    icon: str
    """A Material Symbols name, the set the admin panel already loads."""
    url: str = ""
    """Where the stage is, when another service is it."""
    apps: tuple[str, ...] = ()
    """The admin panel's apps making up the stage, when it is one."""
    landing: tuple[type, ...] = ()
    """The lists in those apps to arrive on, in order of preference: the first the person
    may open. An editor holds no right on users, so System lands them on the model
    versions instead."""
    admits: Callable | None = None
    """Who may enter; defaults to whoever may change something in the apps."""

    @property
    def prefixes(self):
        """The paths a page starts with when it belongs to this stage."""
        return [f"/{settings.CRUDMAN_PATH}/{app}/" for app in self.apps] or [self.url.split("?")[0]]

    def link(self, request):
        """Where the bar sends this person, or None when the stage is not theirs."""
        user = request.user
        if not (self.admits(user) if self.admits else may_change(user, self.apps)):
            return None
        for model in self.landing:
            if admin.site.get_model_admin(model).has_view_or_change_permission(request):
                return reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        return self.url or f"/{settings.CRUDMAN_PATH}/{self.apps[0]}/"


# Everyone: a dashboard is what a viewer is for. Kiosk mode, since the bar is the
# navigation now and Grafana's own chrome would sit inside it a second time. The home
# stage as well, the one the bar's own name leads to.
HOME = Stage("Dashboards", "monitoring", f"/{settings.GRAFANA_PATH}/?kiosk", admits=lambda user: True)

STAGES = (
    HOME,
    Stage("Load", "upload", apps=("dropzones",), landing=(Dropzone,)),
    Stage("Model", "science", f"/{settings.NOTEBOOK_PATH}/", admits=may_use_notebooks),
    # Users and groups are django.contrib.auth's, under the app label "auth" whatever
    # sso/apps.py calls the heading.
    Stage("System", "settings", apps=("auth", "system"), landing=(User, Deployment)),
)


def stage_of(path):
    """The stage a page belongs to, or None outside all of them.

    Args:
        path: The page's path.
    """
    return next(
        (stage for stage in STAGES if any(path.startswith(p) for p in stage.prefixes)),
        None,
    )


def stages_for(request):
    """The stages the bar offers this person, each with where it sends them."""
    return [(stage, url) for stage in STAGES if (url := stage.link(request))]
