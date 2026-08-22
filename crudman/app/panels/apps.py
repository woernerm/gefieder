from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PanelsConfig(AppConfig):
    name = "panels"
    verbose_name = "Panels"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Importing the module is what runs @register_component; without it Unfold has
        # no PanelComponent to instantiate and every embedded panel renders as unknown.
        from . import components  # noqa: F401
        from .examples import create_example_panels

        # Bound to this app so it fires once rather than per installed app. An example
        # already there is left untouched, so an administrator's edits survive a deployment.
        post_migrate.connect(create_example_panels, sender=self)
