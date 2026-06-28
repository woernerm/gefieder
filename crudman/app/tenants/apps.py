from django.apps import AppConfig


class TenantsConfig(AppConfig):
    name = 'tenants'
    # A distinct app label so the sidebar's app heading ("Tenant administration") does
    # not read identically to the model link under it ("Tenants"), which looked like a
    # duplicate menu entry.
    verbose_name = 'Tenant administration'
