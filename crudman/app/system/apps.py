from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "system"
    # What the section is called in the admin: the analytics models, which version of
    # them runs. "System" would repeat the bar's stage the section sits under.
    verbose_name = "Models"
