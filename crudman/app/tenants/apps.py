from django.apps import AppConfig


class TenantsConfig(AppConfig):
    name = 'tenants'
    # Distinct from the model link under it, which would read as a duplicate entry.
    verbose_name = 'System'
