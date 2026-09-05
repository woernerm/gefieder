from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.core.exceptions import PermissionDenied
from django.urls import path, reverse
from unfold.admin import ModelAdmin

from . import repo
from .forms import TenantChangeForm, TenantCreationForm
from .models import Deployment, Tenant
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

    The changelist resyncs the cache table from the live schemas, and every change calls
    the matching PostgreSQL function before the cache row is written.
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

    # The changelist would otherwise show the "no limit" sentinels as bare numbers.
    # @admin.display keeps each column orderable by its underlying field.

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
        # As Django's UserAdmin does: the creation form carries a password field.
        if obj is None:
            kwargs["form"] = self.add_form
        return super().get_form(request, obj, **kwargs)

    def get_queryset(self, request):
        # Refresh the cache so the changelist reflects the schemas that exist.
        sync_tenants()
        return super().get_queryset(request)

    def save_model(self, request, obj, form, change):
        # The cache row is written only once the database side has succeeded.
        if not change and not create_tenant(
            obj.name, form.cleaned_data["password"], obj.display_name
        ):
            messages.error(request, f"Could not create tenant '{obj.name}'.")
            return
        # A rename reaches the schema comment, so a later resync does not revert it; on
        # create, create_tenant has just set it.
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
        # The cache row goes only once PostgreSQL has dropped the tenant.
        if not delete_tenant(obj.name):
            messages.error(request, f"Could not delete tenant '{obj.name}'.")
            return
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        # One at a time, so every tenant goes through delete_tenant.
        for obj in queryset:
            self.delete_model(request, obj)


@admin.register(Deployment)
class DeploymentAdmin(ModelAdmin):
    """The models repository's history, and which commit production is running.

    An admin page rather than one of its own: choosing what the engine computes is
    administration, so it belongs beside the tenants under System and behind the same
    sign-in, not on the documentation pages that are open from the viewer rank up.

    The changelist is replaced wholesale. A row here records a deployment, but the question
    the page answers is "which commit shall run", and the commits come from git rather than
    from this table.
    """

    # A deployment is made by pressing the button below, and an existing one is a record of
    # something that already happened. So there is nothing to add, edit or delete by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        """Readable by whoever may change what runs, and by nobody else.

        Every rank is staff, so the admin's own check would let a viewer in here. The page
        exists to move production between versions, and a viewer's business is the data
        rather than the machinery, so it takes the rank that may change things.
        """
        return request.user.has_perm("system.add_deployment")

    def get_urls(self):
        # Before the base URLs, whose "<path:object_id>/" would otherwise swallow this.
        return [
            path(
                "deploy/",
                self.admin_site.admin_view(self.deploy),
                name="system_deployment_deploy",
            ),
            *super().get_urls(),
        ]

    def changelist_view(self, request, extra_context=None):
        # The base implementation would do this; replacing it wholesale means doing it
        # here, and it is the whole access rule for the page.
        if not self.has_view_permission(request):
            raise PermissionDenied

        current = Deployment.objects.first()
        context = {
            **self.admin_site.each_context(request),
            "title": "Model versions",
            "commits": repo.log(),
            "current": current,
            "current_sha": current.sha if current else None,
            "clone_url": repo.clone_url(),
            "project": repo.PROJECT,
            **(extra_context or {}),
        }
        return TemplateResponse(request, "system/versions.html", context)

    def deploy(self, request):
        """Put the chosen commit into production.

        A pin, so the poll leaves it alone until someone pushes; that push then supersedes
        it, which is why nothing here has to be un-pinned later.

        Not django.views.decorators.http.require_POST: these decorators take the request
        as their first argument, which on a method is self.
        """
        changelist = reverse("admin:system_deployment_changelist")

        # admin_view has already required a signed-in staff account; this is the rank.
        if request.method != "POST" or not request.user.has_perm("system.add_deployment"):
            raise PermissionDenied

        sha = request.POST.get("sha", "").strip()

        if not repo.is_on_branch(sha):
            messages.error(request, "That commit is not on the branch and was not deployed.")
            return redirect(changelist)

        try:
            deployment = repo.deploy(sha, pinned=True, user=request.user)
        except repo.GitError as error:
            messages.error(request, f"The commit could not be deployed: {error}")
            return redirect(changelist)

        if deployment.status == Deployment.FAILED:
            messages.error(request, deployment.message)
        else:
            messages.success(
                request,
                f"Version {deployment.short_sha} is being applied. The engine picks it up "
                f"within a few seconds; this page shows the result.",
            )
        return redirect(changelist)
