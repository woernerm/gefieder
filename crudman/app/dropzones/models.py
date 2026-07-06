import secrets
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse


class Dropzone(models.Model):
    """A named entry point for uploading one specific kind of source files.

    A dropzone describes the purpose and expected format of a set of files, who may
    upload them and what happens on upload: an optional checker function rejects bad
    files, an optional converter function transforms them, and the result is stored in
    a per-upload directory on the uploads volume, recorded by an :class:`Upload` row.
    The unguessable token forms the upload URL, so a link can be handed out without
    creating an account (``require_login`` off).
    """

    class Method(models.TextChoices):
        BROWSER = "browser", "Browser upload"
        API = "api", "API endpoint"
        # Declared so dropzones can already be modelled for it; the SFTP channel itself
        # is not implemented yet. It will feed the same pipeline (services.process_upload)
        # the browser and API uploads use.
        SFTP = "sftp", "SFTP"

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Identifies the dropzone, also in analytics queries. e.g. bank-exports.",
    )
    description = models.TextField(
        blank=True,
        help_text="Purpose and agreed format of the files, shown on the upload page.",
    )
    upload_method = models.CharField(
        max_length=10,
        choices=Method.choices,
        default=Method.BROWSER,
        help_text="How the files arrive. The browser and API uploads are implemented.",
    )
    file_format = models.CharField(
        blank=True,
        max_length=100,
        help_text=(
            'Expected file type(s) as a comma-separated list like ".csv,.xlsx"; '
            "used to preselect matching files in the browser's file dialog."
        ),
    )
    checker = models.CharField(
        blank=True,
        max_length=100,
        help_text="Function that inspects the uploaded files and rejects bad ones.",
    )
    converter = models.CharField(
        blank=True,
        max_length=100,
        help_text="Function that transforms the uploaded files into the stored files.",
    )
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        help_text="Unguessable part of the upload URL.",
    )
    api_token = models.CharField(
        blank=True,
        max_length=64,
        help_text=(
            "Secret for the API endpoint, sent as an 'Authorization: Bearer <token>' "
            "header. An unattended API client cannot log in through the browser, so "
            "when this dropzone requires a login the token stands in for one; leave it "
            "empty to keep the endpoint open (only sensible without a login requirement)."
        ),
    )
    require_login = models.BooleanField(
        default=True,
        help_text="Off: anyone who knows the secret link may upload, without logging in.",
    )
    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="dropzones",
        help_text=(
            "Users who may upload when a login is required. "
            "Empty means every logged-in user; superusers always may."
        ),
    )
    enabled = models.BooleanField(
        default=True,
        help_text="Off: the upload page answers 404, as if the link did not exist.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "dropzone"
        verbose_name_plural = "dropzones"

    def __str__(self):
        return self.name

    def upload_path(self):
        """URL path of the upload page (the secret link without scheme and host)."""
        return reverse("dropzones:upload", kwargs={"token": self.token})

    def upload_url(self):
        """The full secret upload URL to hand to an uploader."""
        scheme = "http" if settings.DEBUG else "https"
        return f"{scheme}://{settings.SERVER_NAME}{self.upload_path()}"

    def api_upload_url(self):
        """The full URL of the API endpoint, for the POST that uploads files."""
        scheme = "http" if settings.DEBUG else "https"
        path = reverse("dropzones:api_upload", kwargs={"token": self.token})
        return f"{scheme}://{settings.SERVER_NAME}{path}"

    def user_may_upload(self, user):
        if not self.require_login:
            return True
        if not user.is_authenticated:
            return False
        if user.is_superuser or not self.allowed_users.exists():
            return True
        return self.allowed_users.filter(pk=user.pk).exists()

    def api_token_matches(self, presented):
        """Whether ``presented`` is the right API token for this dropzone.

        Without a login requirement the URL token alone authorizes the upload, so an
        empty ``api_token`` accepts any client. With a login requirement a token must be
        configured and match; an empty ``api_token`` then rejects every client rather
        than silently opening the endpoint. Compared in constant time so the check does
        not leak the token through its timing.
        """
        if not self.require_login and not self.api_token:
            return True
        if not self.api_token or not presented:
            return False
        return secrets.compare_digest(self.api_token, presented)


class UploadQuerySet(models.QuerySet):
    def valid_at(self, timestamp):
        """The uploads whose validity period covers ``timestamp``, newest first.

        An open bound (NULL) never excludes an upload, so "always valid" and "valid
        until replacement" behave as their names promise. Where periods overlap (e.g.
        a retroactive correction) the newest upload wins, hence the ordering; callers
        wanting exactly one upload take ``.first()``.
        """
        covers_start = Q(valid_from__isnull=True) | Q(valid_from__lte=timestamp)
        covers_end = Q(valid_until__isnull=True) | Q(valid_until__gt=timestamp)
        return self.filter(covers_start & covers_end).order_by("-uploaded_at")


class Upload(models.Model):
    """One uploaded set of files and the period the set is valid for.

    The stored files live in ``directory`` (relative to the uploads volume), one
    :class:`UploadFile` row each. Analytics code finds the file set valid at a given
    timestamp with, mirroring ``UploadQuerySet.valid_at``::

        SELECT u.directory, f.file
        FROM crudman.dropzones_upload u
        JOIN crudman.dropzones_uploadfile f ON f.upload_id = u.id
        JOIN crudman.dropzones_dropzone d ON d.id = u.dropzone_id
        WHERE d.name = 'bank-exports'
          AND (u.valid_from IS NULL OR u.valid_from <= @ts)
          AND (u.valid_until IS NULL OR @ts < u.valid_until)
        ORDER BY u.uploaded_at DESC

    ``f.file`` already contains ``directory``, so the absolute path is the uploads
    volume mount point plus ``f.file``.
    """

    dropzone = models.ForeignKey(
        Dropzone, on_delete=models.PROTECT, related_name="uploads"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Empty for uploads through a secret link without login.",
    )
    valid_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Start of the validity period; empty means valid from the beginning.",
    )
    valid_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the validity period; empty means valid until replaced.",
    )
    directory = models.CharField(
        max_length=255,
        help_text="Directory of the stored files, relative to the uploads volume.",
    )
    sha256 = models.CharField(
        max_length=64,
        help_text="One combined hash over all files as they were uploaded.",
    )

    objects = UploadQuerySet.as_manager()

    class Meta:
        verbose_name = "upload"
        verbose_name_plural = "uploads"
        ordering = ["-uploaded_at"]
        get_latest_by = "uploaded_at"

    def __str__(self):
        return f"{self.dropzone.name} upload {self.uploaded_at or '(unsaved)'}"


def upload_file_path(instance, filename):
    # The per-upload directory was chosen by the pipeline; the storage backend
    # sanitizes the file name part.
    return instance.upload.directory + filename


class UploadFile(models.Model):
    """One stored file of an upload."""

    upload = models.ForeignKey(Upload, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=upload_file_path, max_length=500)

    class Meta:
        verbose_name = "uploaded file"
        verbose_name_plural = "uploaded files"

    def __str__(self):
        # The bare name: the directory is Upload-level information, and the admin
        # shows this string wherever the file is mentioned.
        return Path(self.file.name).name if self.file else "(no file)"


def remove_upload_directory(directory):
    """Delete an upload's directory from the uploads volume, if it still exists.

    Guarded so that only a real subdirectory of MEDIA_ROOT can ever be removed, even
    if ``directory`` is empty or malformed.
    """
    if not directory:
        return
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / directory).resolve()
    if target != root and target.is_relative_to(root) and target.exists():
        shutil.rmtree(target, ignore_errors=True)


@receiver(post_delete, sender=UploadFile)
def _delete_stored_file(sender, instance, **kwargs):
    # Deleting the row (directly or via its upload) removes the file from the volume;
    # Django deliberately does not do this on its own.
    instance.file.delete(save=False)


@receiver(post_delete, sender=Upload)
def _delete_upload_directory(sender, instance, **kwargs):
    # The files are gone by now (the cascade deleted the UploadFile rows first); this
    # removes the then-empty per-upload directory, which FileField.delete leaves behind.
    remove_upload_directory(instance.directory)
