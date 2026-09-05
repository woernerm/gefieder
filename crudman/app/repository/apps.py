from django.apps import AppConfig


class RepositoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "repository"
    verbose_name = "Model versions"
