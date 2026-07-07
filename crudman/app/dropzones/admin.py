from django import forms
from django.conf import settings
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
        # An SFTP login has no unguessable URL token standing in for a credential, so
        # a dropzone with the SFTP method needs its secret (the password) up front.
        if cleaned.get("upload_method") == Dropzone.Method.SFTP and not cleaned.get(
            "secret"
        ):
            self.add_error("secret", "The SFTP upload needs a secret as its password.")
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
    readonly_fields = ("upload_link",)
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
        # What to hand to uploaders: the secret page for a browser dropzone, the POST
        # endpoint (with a ready-to-run curl line) for an API dropzone, the GET URL
        # (with a curl line showing example readings) for a webhook dropzone, the SFTP
        # address (with a ready-to-run sftp line) for an SFTP dropzone. The token
        # exists only once the row is saved.
        if obj is None or not obj.pk:
            return "Available after saving."
        if obj.upload_method == Dropzone.Method.API:
            auth = ' -H "Authorization: Bearer <secret>"' if obj.secret else ""
            return format_html(
                '{}<br><code>curl{} -F files=@yourfile {}</code>',
                obj.api_upload_url(),
                auth,
                obj.api_upload_url(),
            )
        if obj.upload_method == Dropzone.Method.WEBHOOK:
            auth = ' -H "Authorization: Bearer <secret>"' if obj.secret else ""
            return format_html(
                '{}<br><code>curl{} "{}?temperature=21.5"</code>',
                obj.webhook_url(),
                auth,
                obj.webhook_url(),
            )
        if obj.upload_method == Dropzone.Method.SFTP:
            return format_html(
                "{}<br><code>sftp -P {} {}@{}</code>, then <code>put</code> the "
                "file(s) and disconnect; the secret is the password.",
                obj.sftp_address(),
                settings.SFTP_PORT,
                obj.name,
                settings.SERVER_NAME,
            )
        return format_html('<a href="{}">{}</a>', obj.upload_path(), obj.upload_url())


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
