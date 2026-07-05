from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from . import registry
from .models import Dropzone, Upload, UploadFile


def _checker_choices():
    return [("", "No file check")] + [(n, n) for n in registry.checker_names()]


def _converter_choices():
    return [("", "No conversion")] + [(n, n) for n in registry.converter_names()]


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


@admin.register(Dropzone)
class DropzoneAdmin(ModelAdmin):
    form = DropzoneForm
    list_display = (
        "name",
        "upload_method",
        "file_format",
        "checker",
        "converter",
        "enabled",
    )
    list_filter = ("upload_method", "enabled")
    search_fields = ("name", "description")
    filter_horizontal = ("allowed_users",)
    readonly_fields = ("upload_link",)
    fields = (
        "name",
        "description",
        "upload_method",
        "file_format",
        "checker",
        "converter",
        "require_login",
        "allowed_users",
        "enabled",
        "upload_link",
    )

    @admin.display(description="secret upload link")
    def upload_link(self, obj):
        # The link to hand to uploaders. The token exists only once the row is saved.
        if obj is None or not obj.pk:
            return "Available after saving."
        return format_html('<a href="{}">{}</a>', obj.upload_path(), obj.upload_url())


class UploadFileInline(TabularInline):
    model = UploadFile
    extra = 0
    can_delete = False
    # The bare file name plus a link to the authenticated download view, rather than
    # the raw FileField, whose default display would point at an unserved MEDIA URL.
    fields = ("file_name", "download_link")
    readonly_fields = ("file_name", "download_link")

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="file")
    def file_name(self, obj):
        return str(obj) if obj.pk else ""

    @admin.display(description="download")
    def download_link(self, obj):
        if not obj.pk:
            return ""
        return format_html(
            '<a href="{}">Click to download</a>',
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

    def has_add_permission(self, request):
        return False
