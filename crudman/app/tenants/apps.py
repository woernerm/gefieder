from django.apps import AppConfig


class TenantsConfig(AppConfig):
    name = 'tenants'
    # A distinct heading, so the sidebar's app label does not read like the model link
    # under it and look like a duplicate menu entry.
    verbose_name = 'System'
