from pathlib import Path

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

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
        request, "dropzones/upload.html", {"dropzone": dropzone, "form": form}
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
