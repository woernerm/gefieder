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

    The choices are callables, evaluated when the form renders, because the set of
    functions comes from the image at startup rather than from a migration.
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
        # An SFTP or Arrow Flight login has no unguessable URL token standing in for a
        # credential, so those methods need their secret (the password) up front.
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
        # A time period needs its dates from the uploader, and only the browser upload
        # has a form to enter them on.
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

    # The changelist shows the functions by their human-readable labels, like the
    # dropdowns; a name whose function is gone from the image stays visible as-is.
    @admin.display(description="checker", ordering="checker")
    def checker_label(self, obj):
        return dict(registry.checker_choices()).get(obj.checker, obj.checker)

    @admin.display(description="converter", ordering="converter")
    def converter_label(self, obj):
        return dict(registry.converter_choices()).get(obj.converter, obj.converter)

    @admin.display(description="secret upload link")
    def upload_link(self, obj):
        # Just the address to connect to; how to use it is the example field below.
        # The browser link stays clickable, the others are addresses rather than pages.
        # The token exists only once the row is saved.
        if obj is None or not obj.pk:
            return "Available after saving."
        if obj.upload_method == Dropzone.Method.BROWSER:
            return format_html('<a href="{}">{}</a>', obj.upload_path(), obj.upload_url())
        return obj.upload_address()

    @admin.display(description="example")
    def example(self, obj):
        # The ready-to-run client for the chosen method, filled in with this dropzone's
        # address and credentials. white-space:pre keeps the indentation intact.
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
    # A single column per file, linking to the authenticated download view rather than
    # the raw FileField, whose default display would point at an unserved MEDIA URL.
    # text-link is the class Unfold puts on its own readonly links (e.g. the uploaded_by
    # user), so the link matches the admin's link styling.
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
    """Uploads are created by the upload pipeline, never by hand, so adding is off and
    most fields are read-only. The validity dates stay editable for corrections."""

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
        # Django's own delete view, so the confirmation page and the permission
        # checks stay in charge; this only saves opening the upload first.
        return format_html(
            '<a href="{}">Delete</a>',
            reverse("admin:dropzones_upload_delete", args=[obj.pk]),
        )

    def has_add_permission(self, request):
        return False
