from django.apps import AppConfig


class PanelsConfig(AppConfig):
    name = "panels"
    verbose_name = "Panels"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Importing the module is what runs @register_component; without it Unfold has
        # no PanelComponent to instantiate and every embedded panel renders as unknown.
        from . import components  # noqa: F401
