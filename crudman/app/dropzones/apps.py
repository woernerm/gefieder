from django.apps import AppConfig


class DropzonesConfig(AppConfig):
    name = "dropzones"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # New functions only arrive with a rebuilt image, so discovering them once at
        # startup is sufficient.
        from . import registry

        registry.autodiscover()
