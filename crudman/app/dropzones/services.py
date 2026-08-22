"""The upload pipeline, shared by every upload method.

``process_upload`` takes no request object so that the browser view and the SFTP and
Flight endpoints all feed the same pipeline: spool the incoming files to a temporary
directory, run the dropzone's checker and converter, then store the resulting files and
the Upload row all-or-nothing.
"""

import hashlib
import tempfile
import uuid
from pathlib import Path

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from . import registry
from .models import Upload, UploadFile, remove_upload_directory


class UploadError(Exception):
    """An upload was rejected; the message is shown to the uploading user."""


def process_upload(dropzone, files, valid_from=None, valid_until=None, user=None):
    """Run the pipeline for one uploaded set of files.

    Args:
        dropzone: The dropzone that was uploaded to.
        files: Django ``File`` objects, e.g. a form's ``UploadedFile`` list.
        valid_from: Start of the upload's validity, or None for no lower bound.
        valid_until: End of the upload's validity, or None for no upper bound.
        user: The uploading user, if the upload came through an authenticated session.

    Returns:
        The stored Upload.

    Raises:
        UploadError: The files were empty, or the checker or converter rejected them;
            nothing is stored in that case.
    """
    if not files:
        raise UploadError("The upload contains no files.")
    with tempfile.TemporaryDirectory() as tmp:
        in_dir = Path(tmp) / "in"
        out_dir = Path(tmp) / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        in_paths = _spool(files, in_dir)
        # Hash the files as uploaded, before any conversion, so the hash identifies the
        # source data and stays stable when a converter changes later.
        digest = _combined_hash(in_paths)
        _run_checker(dropzone, in_paths)
        stored_paths = _run_converter(dropzone, in_paths, out_dir)
        return _store(dropzone, stored_paths, digest, valid_from, valid_until, user)


def _spool(files, in_dir):
    """Write the incoming file objects to disk.

    The check and convert functions work on real paths rather than streams.

    Args:
        files: The incoming Django ``File`` objects.
        in_dir: Directory to write them into.

    Returns:
        The written paths, in upload order.
    """
    paths = []
    for file in files:
        # Strip any client-supplied directory parts; a name collision within one upload
        # is kept apart with a numeric suffix.
        name = Path(file.name or "upload").name
        target = in_dir / name
        counter = 0
        while target.exists():
            counter += 1
            target = in_dir / f"{Path(name).stem}_{counter}{Path(name).suffix}"
        with target.open("wb") as spooled:
            for chunk in file.chunks():
                spooled.write(chunk)
        paths.append(target)
    return paths


def _combined_hash(paths):
    """One hash covering all files, independent of the upload order.

    Args:
        paths: The files to hash.

    Returns:
        The sha256 of the sorted per-file sha256 digests.
    """
    digests = []
    for path in paths:
        file_hash = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1 << 20):
                file_hash.update(chunk)
        digests.append(file_hash.hexdigest())
    return hashlib.sha256("".join(sorted(digests)).encode()).hexdigest()


def _run_checker(dropzone, in_paths):
    if not dropzone.checker:
        return
    try:
        check = registry.get_checker(dropzone.checker)
    except LookupError as error:
        raise UploadError(str(error)) from error
    try:
        check(list(in_paths))
    except Exception as error:
        # Any exception means rejection, per the checker contract; its message is what
        # the uploading user gets to see.
        raise UploadError(str(error) or "The files were rejected.") from error


def _run_converter(dropzone, in_paths, out_dir):
    if not dropzone.converter:
        return in_paths
    try:
        convert = registry.get_converter(dropzone.converter)
    except LookupError as error:
        raise UploadError(str(error)) from error
    try:
        convert(list(in_paths), out_dir)
    except Exception as error:
        raise UploadError(str(error) or "The files could not be converted.") from error
    converted = sorted(path for path in out_dir.iterdir() if path.is_file())
    if not converted:
        raise UploadError("The conversion produced no files.")
    return converted


def _store(dropzone, paths, digest, valid_from, valid_until, user):
    """Write the Upload row, its files and the validity clipping all-or-nothing."""
    directory = "dropzones/{}/{}_{}/".format(
        dropzone.pk, timezone.now().strftime("%Y%m%dT%H%M%S"), uuid.uuid4().hex[:8]
    )
    try:
        with transaction.atomic():
            upload = Upload.objects.create(
                dropzone=dropzone,
                uploaded_by=user if user and user.is_authenticated else None,
                valid_from=valid_from,
                valid_until=valid_until,
                directory=directory,
                sha256=digest,
            )
            for path in paths:
                stored = UploadFile(upload=upload)
                with path.open("rb") as stream:
                    stored.file.save(path.name, File(stream))
            _clip_replaced(upload)
        return upload
    except Exception:
        # The FileField writes happen while the transaction is still open, so a
        # failure rolls back the rows but would leave the files behind — remove them.
        remove_upload_directory(directory)
        raise


def _clip_replaced(upload):
    """Shorten previously open-ended uploads to end where the new upload starts.

    "Always valid" uploads (no start) stay untouched: they act as a fallback and are
    superseded by the newest-wins ordering of ``UploadQuerySet.valid_at`` instead.

    Args:
        upload: The newly stored upload.
    """
    if upload.valid_from is None:
        return
    Upload.objects.filter(
        dropzone=upload.dropzone,
        valid_from__isnull=False,
        valid_until__isnull=True,
        valid_from__lt=upload.valid_from,
    ).exclude(pk=upload.pk).update(valid_until=upload.valid_from)
