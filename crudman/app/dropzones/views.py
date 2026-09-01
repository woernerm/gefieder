import csv
import io
import re
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .forms import UploadForm
from .models import Dropzone, UploadFile
from .services import UploadError, process_upload


def upload(request, token):
    """The browser upload page behind a dropzone's secret link.

    Args:
        request: The HTTP request; a POST carries the files and validity fields.
        token: The dropzone's secret token from the URL.

    Returns:
        The rendered page, or a redirect after an accepted upload. A disabled dropzone or
        one of another method answers 404, like an unknown token, so nothing reveals
        whether a link exists.
    """
    dropzone = get_object_or_404(
        Dropzone, token=token, enabled=True, upload_method=Dropzone.Method.BROWSER
    )
    if not dropzone.user_may_upload(request.user):
        if not request.user.is_authenticated:
            # Through the admin login and back to this page.
            return redirect_to_login(request.get_full_path(), reverse("admin:login"))
        raise PermissionDenied
    form = UploadForm(dropzone=dropzone)
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES, dropzone=dropzone)
        if form.is_valid():
            try:
                result = process_upload(
                    dropzone,
                    form.cleaned_data["files"],
                    valid_from=form.cleaned_data["valid_from"],
                    valid_until=form.cleaned_data["valid_until"],
                    user=request.user if request.user.is_authenticated else None,
                )
            except UploadError as error:
                # A form-wide error, so the uploader can fix the files and retry.
                form.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    f"Upload accepted, {result.files.count()} file(s) stored.",
                )
                # So refreshing cannot re-submit the files.
                return redirect(request.path)
    return render(
        request,
        "dropzones/upload.html",
        # APP_NAME is the page's headline as everywhere else; the dropzone name is the
        # subheading.
        {"dropzone": dropzone, "form": form, "app_name": settings.APP_NAME},
    )


def download(request, pk):
    """Stream a stored file to a logged-in admin user.

    There is no public MEDIA_URL, so this is the only way a stored file leaves the
    volume over HTTP. Staff only: uploading through a secret link must not imply
    permission to read what others uploaded.

    Args:
        request: The HTTP request.
        pk: Primary key of the :class:`UploadFile` to stream.

    Returns:
        The file as an attachment, or a redirect to the admin login.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("admin:login"))
    if not request.user.is_staff:
        raise PermissionDenied
    stored = get_object_or_404(UploadFile, pk=pk)
    try:
        handle = stored.file.open("rb")
    except FileNotFoundError:
        raise Http404("The stored file is missing from the uploads volume.")
    return FileResponse(
        handle, as_attachment=True, filename=Path(stored.file.name).name
    )


def _bearer_token(request):
    """The token from an ``Authorization: Bearer <token>`` header, or an empty string."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _refusal(request, dropzone, verb, wrong_verb_message):
    """The error answer an unattended upload gets before it is even read, or None.

    Both endpoints accept exactly one verb and authenticate by bearer token, so the two
    refusals are the same question asked of a different verb.

    Args:
        request: The incoming request.
        dropzone: The dropzone it addresses.
        verb: The only method this endpoint answers.
        wrong_verb_message: What to say to any other method.

    Returns:
        A 405 or 401 JSON response, or None when the call may proceed.
    """
    if request.method != verb:
        return JsonResponse({"error": wrong_verb_message}, status=405)
    if not dropzone.api_secret_matches(_bearer_token(request)):
        return JsonResponse({"error": "Invalid or missing API token."}, status=401)
    return None


def _stored(upload):
    """The 201 an accepted upload answers with, identical for every JSON endpoint."""
    return JsonResponse(
        {
            "upload_id": upload.pk,
            "files": upload.files.count(),
            "sha256": upload.sha256,
            "valid_from": upload.valid_from,
            "valid_until": upload.valid_until,
        },
        status=201,
    )


def _api_validity(post, default):
    """Turn the API's validity fields into the ``(valid_from, valid_until)`` pair.

    Mirrors ``UploadForm.clean``, so the API rejects bad input as the browser form does.

    Args:
        post: The POST data, carrying ``validity``: ``until_replaced`` (starts now),
            ``always`` (both bounds open) or ``period`` (optional ISO 8601
            ``valid_from`` and ``valid_until``, an empty start meaning now).
        default: The dropzone's default validity, used when no mode was sent.

    Returns:
        The ``(valid_from, valid_until)`` pair, either bound possibly None.

    Raises:
        UploadError: Unknown mode, unparseable date, or an end not after the start.
    """
    mode = post.get("validity") or default
    if mode == UploadForm.ALWAYS:
        return None, None
    if mode == UploadForm.UNTIL_REPLACED:
        return timezone.now(), None
    if mode != UploadForm.PERIOD:
        raise UploadError(f"Unknown validity '{mode}'.")

    def parse(field):
        raw = post.get(field, "").strip()
        if not raw:
            return None
        value = parse_datetime(raw)
        if value is None:
            raise UploadError(f"'{field}' is not a valid ISO 8601 date-time.")
        # A naive value is read in the server's timezone, as the form does.
        return value if timezone.is_aware(value) else timezone.make_aware(value)

    start = parse("valid_from") or timezone.now()
    end = parse("valid_until")
    if end and end <= start:
        raise UploadError("'valid_until' must be after 'valid_from'.")
    return start, end


@csrf_exempt
def api_upload(request, token):
    """Accept an upload over HTTP POST for a dropzone whose method is the API endpoint.

    CSRF is exempt: the caller is a script authenticated by a bearer token, not a
    browser carrying cookies.

    Args:
        request: The POST, carrying the files under ``files``, the optional validity
            fields, and the dropzone's secret as ``Authorization: Bearer``.
        token: The dropzone's secret token from the URL.

    Returns:
        JSON describing the stored upload (201), or an error (400, 401, 405). A
        disabled dropzone or one of another method answers 404, like an unknown token.
    """
    dropzone = get_object_or_404(
        Dropzone, token=token, enabled=True, upload_method=Dropzone.Method.API
    )
    if refusal := _refusal(request, dropzone, "POST", "Use POST to upload."):
        return refusal
    files = request.FILES.getlist("files")
    try:
        valid_from, valid_until = _api_validity(request.POST, dropzone.default_validity)
        # No user, as with a secret-link browser upload.
        upload = process_upload(
            dropzone, files, valid_from=valid_from, valid_until=valid_until
        )
    except UploadError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return _stored(upload)


WEBHOOK_MAX_PARAMS = 100
"""Most query parameters one webhook call may carry.

A device sends a handful of readings, so anything past this is not data.
"""

WEBHOOK_MAX_VALUE_LENGTH = 1000
"""Longest value one webhook query parameter may carry."""

# The names become CSV column names read by analytics code, so only ones that stay
# unremarkable in Polars and SQL are accepted.
_WEBHOOK_NAME = re.compile(r"[A-Za-z0-9_]+")


def _webhook_file(query):
    """A webhook call's query parameters as a one-row CSV file, ready for the pipeline.

    Args:
        query: The call's query parameters.

    Returns:
        A CSV file whose header is the sorted parameter names, so the column order does
        not depend on how the device arranges its URL, and whose single row holds the
        values as they arrived.

    Raises:
        UploadError: The parameters could not have come from a well-configured device.
    """
    if not query:
        raise UploadError("The request carries no query parameters.")
    if len(query) > WEBHOOK_MAX_PARAMS:
        raise UploadError(f"More than {WEBHOOK_MAX_PARAMS} query parameters.")
    row = {}
    for name in query:
        if not _WEBHOOK_NAME.fullmatch(name):
            raise UploadError(
                f"Invalid parameter name '{name}': letters, digits and _ only."
            )
        values = query.getlist(name)
        if len(values) > 1:
            raise UploadError(f"Duplicate parameter '{name}'.")
        if len(values[0]) > WEBHOOK_MAX_VALUE_LENGTH:
            raise UploadError(
                f"The value of '{name}' exceeds {WEBHOOK_MAX_VALUE_LENGTH} characters."
            )
        row[name] = values[0]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(sorted(row))
    writer.writerow([row[name] for name in sorted(row)])
    return ContentFile(buffer.getvalue().encode(), name="webhook.csv")


@csrf_exempt
def webhook_upload(request, token):
    """Accept readings as query parameters of an HTTP GET and store them as one CSV.

    Made for devices that can only call a URL with measured values substituted into it.
    A GET with a side effect is deliberate, being the only verb such devices speak. CSRF
    is exempt so a stray POST gets a 405 rather than a misleading CSRF error.

    Args:
        request: The GET, its query parameters carrying the readings and its optional
            ``Authorization: Bearer`` header the dropzone's secret.
        token: The dropzone's secret token from the URL.

    Returns:
        JSON describing the stored upload (201), or an error (400, 401, 405). A
        disabled dropzone or one whose method is not the webhook answers 404.
    """
    dropzone = get_object_or_404(
        Dropzone, token=token, enabled=True, upload_method=Dropzone.Method.WEBHOOK
    )
    if refusal := _refusal(request, dropzone, "GET", "Use GET with query parameters."):
        return refusal
    try:
        # The query string is payload, so a call carries no validity fields and the
        # dropzone's default applies, as for an SFTP upload.
        upload = process_upload(
            dropzone,
            [_webhook_file(request.GET)],
            valid_from=dropzone.default_valid_from(),
        )
    except UploadError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return _stored(upload)
