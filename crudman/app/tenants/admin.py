from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from .forms import TenantChangeForm, TenantCreationForm
from .models import Tenant
from .utils import (
    create_tenant,
    delete_tenant,
    set_tenant_display_name,
    set_tenant_limits,
    sync_tenants,
)


@admin.register(Tenant)
class TenantAdmin(ModelAdmin):
    """Admin for the ``Tenant`` model.

    The changelist resyncs the cache table from the live schemas, and creating, editing
    or deleting a tenant calls the matching PostgreSQL function before the cache row is
    written. Only documented Django admin hooks are overridden.
    """

    list_display = (
        "display_name",
        "connection_limit_display",
        "statement_timeout_display",
        "work_mem_display",
        "temp_file_limit_display",
    )
    search_fields = ("name", "display_name")
    form = TenantChangeForm
    add_form = TenantCreationForm

    # The changelist would otherwise show the "no limit" sentinels as bare numbers. Only
    # those are replaced with "infinite"; @admin.display keeps each column orderable by
    # its underlying field.

    @admin.display(description="connection limit", ordering="connection_limit")
    def connection_limit_display(self, obj):
        if obj.connection_limit == Tenant.UNLIMITED_COUNT:
            return "infinite"
        return obj.connection_limit

    @admin.display(description="statement timeout", ordering="statement_timeout")
    def statement_timeout_display(self, obj):
        return self._size_or_infinite(obj.statement_timeout)

    @admin.display(description="work memory", ordering="work_mem")
    def work_mem_display(self, obj):
        return self._size_or_infinite(obj.work_mem)

    @admin.display(description="temp file limit", ordering="temp_file_limit")
    def temp_file_limit_display(self, obj):
        return self._size_or_infinite(obj.temp_file_limit)

    @staticmethod
    def _size_or_infinite(value):
        """A size or time limit, or "infinite" for the unlimited sentinel "0"."""
        return "infinite" if value == Tenant.UNLIMITED_SIZE else value

    def get_form(self, request, obj=None, **kwargs):
        # Mirror Django's UserAdmin: use the creation form (with a password field) when
        # adding, and the change form when editing.
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, **kwargs)

    def get_queryset(self, request):
        # Refresh the cache from the live schemas so the changelist reflects the tenants
        # that actually exist in PostgreSQL, then hand back a normal queryset.
        sync_tenants()
        return super().get_queryset(request)

    def save_model(self, request, obj, form, change):
        # The cache row is written only once the database side has succeeded.
        if not change and not create_tenant(
            obj.name, form.cleaned_data["password"], obj.display_name
        ):
            messages.error(request, f"Could not create tenant '{obj.name}'.")
            return
        # On edit, propagate a renamed display name to the schema comment so a later
        # resync does not revert it; on create, create_tenant just set it.
        if change and not set_tenant_display_name(obj.name, obj.display_name):
            messages.warning(
                request,
                f"Tenant '{obj.name}' saved, but its display name could not be updated.",
            )
        if not set_tenant_limits(
            obj.name,
            obj.connection_limit,
            obj.statement_timeout,
            obj.work_mem,
            obj.temp_file_limit,
        ):
            messages.warning(
                request, f"Tenant '{obj.name}' saved, but its limits could not be applied."
            )
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        # Drop the tenant in PostgreSQL first; remove the cache row only if that worked.
        if not delete_tenant(obj.name):
            messages.error(request, f"Could not delete tenant '{obj.name}'.")
            return
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # One at a time, so every tenant goes through delete_tenant rather than a single
        # SQL DELETE on the cache table.
        for obj in queryset:
            self.delete_model(request, obj)
