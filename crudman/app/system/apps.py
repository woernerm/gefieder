from django.apps import AppConfig


class SystemConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "system"
    # What the section is called in the admin. The models under it are named for what they
    # are -- tenants, model versions -- so the heading says what they have in common: this
    # is where the system itself is administered rather than the data in it.
    verbose_name = "System"
