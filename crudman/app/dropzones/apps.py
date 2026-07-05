from django.apps import AppConfig


class DropzonesConfig(AppConfig):
    name = "dropzones"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Import the check/convert functions once so their decorators register them.
        # New functions only arrive with a rebuilt image, so discovering them once at
        # startup is sufficient.
        from . import registry

        registry.autodiscover()
