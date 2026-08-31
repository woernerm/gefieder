from django.apps import AppConfig


class DropzonesConfig(AppConfig):
    name = "dropzones"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # New functions arrive only with a rebuilt image, so once at startup is enough.
        from . import registry

        registry.autodiscover()
