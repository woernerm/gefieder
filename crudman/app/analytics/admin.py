"""Authoring panels.

Whoever may add or change a panel decides what SQL runs as the analytics role, which
reads every medallion layer including every tenant's bronze schema. That is an
analyst-level right rather than an editorial one, which is why "analytics" is absent from
``sso.roles.MANAGED_APPS``: the three provider ranks carry no panel permission at all,
and someone has to be given it deliberately (a superuser holds it inherently).
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Panel


@admin.register(Panel)
class PanelAdmin(ModelAdmin):
    list_display = ("title", "slug", "on_dashboard")
    list_filter = ("on_dashboard",)
    search_fields = ("title", "slug", "sql")
    prepopulated_fields = {"slug": ("title",)}

    fieldsets = (
        (None, {"fields": ("title", "slug", "description", "on_dashboard")}),
        ("Query", {"fields": ("sql", "parameters")}),
        ("Chart", {"fields": ("options", "height")}),
    )
