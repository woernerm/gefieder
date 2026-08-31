from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from . import registry
from .models import Dropzone, Upload, UploadFile


def _checker_choices():
    return [("", "No file check")] + registry.checker_choices()


def _converter_choices():
    return [("", "No conversion")] + registry.converter_choices()


class DropzoneForm(forms.ModelForm):
    """Offers the registered check/convert functions as dropdowns.

    The choices are callables, evaluated at render time, the set of functions coming from
    the image at startup rather than from a migration.
    """

    checker = forms.ChoiceField(
        choices=_checker_choices,
        required=False,
        help_text=Dropzone._meta.get_field("checker").help_text,
    )
    converter = forms.ChoiceField(
        choices=_converter_choices,
        required=False,
        help_text=Dropzone._meta.get_field("converter").help_text,
    )

    class Meta:
        model = Dropzone
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        # No unguessable URL token stands in for a credential on these methods, so the
        # secret is their password and is needed up front.
        if cleaned.get("upload_method") == Dropzone.Method.SFTP and not cleaned.get(
            "secret"
        ):
            self.add_error("secret", "The SFTP upload needs a secret as its password.")
        if cleaned.get("upload_method") == Dropzone.Method.FLIGHT and not cleaned.get(
            "secret"
        ):
            self.add_error(
                "secret", "The Arrow Flight upload needs a secret as its password."
            )
        # Only the browser upload has a form to enter the dates on.
        if (
            cleaned.get("default_validity") == Dropzone.Validity.PERIOD
            and cleaned.get("upload_method") != Dropzone.Method.BROWSER
        ):
            self.add_error(
                "default_validity",
                "A given time period is only available for the browser upload.",
            )
        return cleaned


@admin.register(Dropzone)
class DropzoneAdmin(ModelAdmin):
    form = DropzoneForm
    list_display = (
        "name",
        "upload_method",
        "file_format",
        "checker_label",
        "converter_label",
        "enabled",
    )
    list_filter = ("upload_method", "enabled")
    search_fields = ("name", "description")
    filter_horizontal = ("allowed_users",)
    readonly_fields = ("upload_link", "example")
    fields = (
        "name",
        "description",
        "upload_method",
        "file_format",
        "checker",
        "converter",
        "default_validity",
        "require_login",
        "allowed_users",
        "secret",
        "enabled",
        "upload_link",
        "example",
    )

    # As in the dropdowns; a name whose function left the image stays visible as it is.
    @admin.display(description="checker", ordering="checker")
    def checker_label(self, obj):
        return dict(registry.checker_choices()).get(obj.checker, obj.checker)

    @admin.display(description="converter", ordering="converter")
    def converter_label(self, obj):
        return dict(registry.converter_choices()).get(obj.converter, obj.converter)

    @admin.display(description="secret upload link")
    def upload_link(self, obj):
        # The browser link stays clickable, the others being addresses rather than
        # pages. The token exists only once the row is saved.
        if obj is None or not obj.pk:
            return "Available after saving."
        if obj.upload_method == Dropzone.Method.BROWSER:
            return format_html('<a href="{}">{}</a>', obj.upload_path(), obj.upload_url())
        return obj.upload_address()

    @admin.display(description="example")
    def example(self, obj):
        # white-space:pre keeps the indentation intact.
        if obj is None or not obj.pk:
            return "Available after saving."
        return format_html(
            '<pre style="white-space: pre; overflow-x: auto">{}</pre>',
            obj.upload_example(),
        )


class UploadFileInline(TabularInline):
    model = UploadFile
    extra = 0
    can_delete = False
    # The authenticated download view rather than the raw FileField, whose default
    # display would point at an unserved MEDIA URL.
    fields = ("file_link",)
    readonly_fields = ("file_link",)

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="file")
    def file_link(self, obj):
        if not obj.pk:
            return ""
        return format_html(
            '<a href="{}" class="text-link">Click to download ⤓</a>',
            reverse("dropzones:download", kwargs={"pk": obj.pk}),
        )


@admin.register(Upload)
class UploadAdmin(ModelAdmin):
    """Read-only view of the uploads the pipeline created.

    Uploads are never made by hand, so adding is off; only the validity dates stay
    editable, for corrections.
    """

    list_display = (
        "dropzone",
        "uploaded_at",
        "uploaded_by",
        "valid_from",
        "valid_until",
        "short_hash",
        "delete_link",
    )
    list_filter = ("dropzone",)
    readonly_fields = ("dropzone", "uploaded_at", "uploaded_by", "directory", "sha256")
    fields = (
        "dropzone",
        "uploaded_at",
        "uploaded_by",
        "valid_from",
        "valid_until",
        "directory",
        "sha256",
    )
    inlines = (UploadFileInline,)

    @admin.display(description="sha256", ordering="sha256")
    def short_hash(self, obj):
        return obj.sha256[:12]

    @admin.display(description="delete")
    def delete_link(self, obj):
        # Django's own delete view, so its confirmation page and permission checks stay
        # in charge.
        return format_html(
            '<a href="{}">Delete</a>',
            reverse("admin:dropzones_upload_delete", args=[obj.pk]),
        )

    def has_add_permission(self, request):
        return False
