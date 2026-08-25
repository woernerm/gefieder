"""Authoring queries, charts, panels and dashboards.

Whoever may add or change a query decides what SQL runs as the analytics role, which
reads every medallion layer including every tenant's bronze schema. That is an
analyst-level right rather than an editorial one, which is why "analytics" is absent from
``sso.roles.MANAGED_APPS``: the three provider ranks carry no permission here at all, and
someone has to be given it deliberately (a superuser holds it inherently).

The split shows up in the forms as one convenience. A chart names placeholders and a query
returns columns, so a panel has to say which is which; asking for that from a blank field
would make reuse feel like a cost. Saving a panel with no bindings fills them in from the
query's probed columns, and the proposal is then an ordinary editable value -- see
``PanelAdmin.save_model`` and ``analytics.bindings``.
"""

from django.contrib import admin, messages
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from unfold.admin import ModelAdmin

from .models import Chart, Dashboard, Panel, Query


@admin.register(Query)
class QueryAdmin(ModelAdmin):
    list_display = ("title", "column_summary")
    search_fields = ("title", "sql")
    readonly_fields = ("column_summary",)

    fieldsets = (
        (None, {"fields": ("title", "description")}),
        ("Query", {"fields": ("sql", "parameter_defaults", "column_summary")}),
        ("Checks", {"fields": ("checks",)}),
    )

    @admin.display(description="columns")
    def column_summary(self, instance):
        """The probed signature, which is what a panel's bindings may choose from.

        Reading ``columns`` rather than ``signature`` so that a query created before the
        tables it reads existed establishes its own signature here, on first sight.
        """
        if not instance.columns:
            return "not probed -- save to refresh, or fix the statement"
        return ", ".join(
            f"{column['name']} ({column['kind']})" for column in instance.signature
        )


@admin.register(Chart)
class ChartAdmin(ModelAdmin):
    list_display = ("title", "placeholder_summary")
    search_fields = ("title",)

    fieldsets = (
        (None, {"fields": ("title", "description")}),
        ("Chart", {"fields": ("options", "transforms")}),
    )

    @admin.display(description="placeholders")
    def placeholder_summary(self, instance):
        """Which placeholders a panel using this chart will have to bind."""
        placeholders = instance.placeholders
        if not placeholders:
            return "none -- this chart names no ${placeholder}"
        return ", ".join(
            f"${{{name}}}" + ("[] (a list)" if is_list else "")
            for name, is_list in placeholders.items()
        )


class PanelInline(admin.TabularInline):
    """The panels on a dashboard, in the order they are laid out."""

    model = Panel
    extra = 0
    fields = ("order", "title", "query", "chart", "span", "height")
    ordering = ("order",)
    show_change_link = True

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Hand back the bare select, without the wrapper's add/change/delete/view icons.

        Django wraps every related field in a RelatedFieldWidgetWrapper, whose four icons
        are all wrong here: a query or a chart is authored on its own page, not invented
        while laying out a dashboard. Unwrapped here rather than in
        ``formfield_for_foreignkey``, which runs before the wrapping and so cannot undo it.
        """
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name in ("query", "chart") and isinstance(
            field.widget, RelatedFieldWidgetWrapper
        ):
            field.widget = field.widget.widget
        return field


@admin.register(Dashboard)
class DashboardAdmin(ModelAdmin):
    list_display = ("title", "name", "columns", "panel_count")
    search_fields = ("title", "name")
    inlines = (PanelInline,)

    @admin.display(description="panels")
    def panel_count(self, instance):
        return instance.panels.count()


@admin.register(Panel)
class PanelAdmin(ModelAdmin):
    list_display = ("__str__", "dashboard", "query", "chart", "span")
    list_filter = ("dashboard", "chart")
    search_fields = ("title",)
    autocomplete_fields = ("query", "chart")
    readonly_fields = ("binding_help",)

    fieldsets = (
        (None, {"fields": ("title", "description")}),
        ("Sources", {"fields": ("query", "chart", "parameters")}),
        ("Shaping", {"fields": ("transforms",)}),
        ("Bindings", {"fields": ("bindings", "binding_help")}),
        ("Placement", {"fields": ("dashboard", "order", "span", "height")}),
    )

    @admin.display(description="available columns")
    def binding_help(self, instance):
        """What a binding may name: the query's columns after this panel's shaping."""
        if not instance.pk:
            return "Save once and the bindings below are proposed for you."
        columns = instance.available_columns
        if not columns:
            return "The query has not been probed; open it and save it to refresh."
        return ", ".join(
            column["name"] + (f" ({column['kind']})" if column["kind"] else "")
            for column in columns
        )

    def save_model(self, request, obj, form, change):
        """Fill empty bindings from the query's columns, then save.

        Only when they are empty: a proposal is a starting point, and overwriting an
        author's own mapping on every save would make the field impossible to edit.
        """
        super().save_model(request, obj, form, change)

        if obj.bindings or not obj.chart.placeholders:
            return

        proposal = obj.propose_bindings()
        if not proposal:
            return

        obj.bindings = proposal
        obj.save(update_fields=["bindings"])
        messages.info(
            request,
            "Bindings proposed from the query's columns: "
            + ", ".join(f"${{{placeholder}}} -> {column}" for placeholder, column in proposal.items())
            + ". Change any that are wrong.",
        )
