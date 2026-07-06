from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
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

    A disabled dropzone or one whose method is not the browser upload answers 404,
    exactly like an unknown token, so the page never reveals whether a link exists.
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
                # The checker/converter verdict; shown as a form-wide error so the
                # uploader can fix the files and try again.
                form.add_error(None, str(error))
            else:
                messages.success(
                    request,
                    f"Upload accepted, {result.files.count()} file(s) stored.",
                )
                # Redirect after POST, so refreshing cannot re-submit the files.
                return redirect(request.path)
    return render(
        request,
        "dropzones/upload.html",
        # APP_NAME (from buildtime.env) is the page's headline, like everywhere else
        # in the system; the dropzone name becomes the subheading.
        {"dropzone": dropzone, "form": form, "app_name": settings.APP_NAME},
    )


def download(request, pk):
    """Stream a stored file to a logged-in admin user.

    The admin links here for every stored file; there is no public MEDIA_URL, so this
    authenticated view is the only way a stored file leaves the uploads volume over
    HTTP. Restricted to staff because uploading through a secret link must not imply
    permission to read what others uploaded.
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


def _api_validity(post, default):
    """Turn the API's validity fields into the ``(valid_from, valid_until)`` pair.

    Mirrors ``UploadForm.clean``: ``validity`` is one of ``until_replaced`` (starts
    now), ``always`` (both open) or ``period`` (optional ``valid_from`` /
    ``valid_until`` as ISO 8601, empty start meaning now); a request that sends no
    mode gets ``default``, the dropzone's default validity. Raises
    :class:`UploadError` on an unknown mode, an unparseable date or an end that is
    not after the start, so the API rejects bad input the same way the browser form
    does.
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
        # A naive value is read in the server's timezone, as the browser form does.
        return value if timezone.is_aware(value) else timezone.make_aware(value)

    start = parse("valid_from") or timezone.now()
    end = parse("valid_until")
    if end and end <= start:
        raise UploadError("'valid_until' must be after 'valid_from'.")
    return start, end


@csrf_exempt
def api_upload(request, token):
    """Accept an upload over HTTP POST for a dropzone whose method is the API endpoint.

    The URL carries the same secret token as the browser link; the API token (if the
    dropzone requires a login) travels in an ``Authorization: Bearer`` header, because
    an unattended client cannot hold a session. The multipart body carries the files
    under ``files`` and the optional ``validity`` / ``valid_from`` / ``valid_until``
    fields; on success the files run through the very same pipeline as a browser upload.

    A disabled dropzone or one whose method is not the API answers 404, exactly like an
    unknown token, so the endpoint never reveals whether a link exists. CSRF is exempt
    because the caller is a script authenticated by a bearer token, not a browser
    carrying cookies.
    """
    dropzone = get_object_or_404(
        Dropzone, token=token, enabled=True, upload_method=Dropzone.Method.API
    )
    if request.method != "POST":
        return JsonResponse({"error": "Use POST to upload."}, status=405)
    if not dropzone.api_secret_matches(_bearer_token(request)):
        return JsonResponse({"error": "Invalid or missing API token."}, status=401)
    files = request.FILES.getlist("files")
    try:
        valid_from, valid_until = _api_validity(request.POST, dropzone.default_validity)
        # API uploads carry no user, like a secret-link browser upload.
        upload = process_upload(
            dropzone, files, valid_from=valid_from, valid_until=valid_until
        )
    except UploadError as error:
        return JsonResponse({"error": str(error)}, status=400)
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
