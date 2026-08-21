"""What the SFTP and Arrow Flight endpoints have in common.

Both run as their own long-lived server process outside the request cycle, authenticate an
uploader against a dropzone rather than a Django session, collect files in a throwaway
directory and hand the finished set to ``services.process_upload``. Only the protocol
differs, so everything either one does with the database lives here and each endpoint is
left with its own protocol handling.
"""

from django.core.files import File
from django.db import close_old_connections, connections
from django.utils import timezone

from .models import Dropzone
from .services import process_upload


def fresh(func, *args):
    """Run a database-facing function on a healthy connection, and leave none behind.

    The servers run for weeks, so a connection the database dropped in the meantime
    (restart, idle timeout) is discarded before the call instead of failing it.

    Both endpoints answer on worker threads -- asgiref's for SFTP, Flight's own pool -- and
    a connection opened on one of those belongs to that thread forever: nothing signals the
    end of the call the way a request does for a view, so it would be held until the process
    exits. Closing afterwards keeps the endpoints down to the connections they are actually
    using, which is also what lets the test database be dropped at the end of a test run.
    """
    close_old_connections()
    try:
        return func(*args)
    finally:
        connections.close_all()


def authenticate(method, name, secret):
    """The dropzone the credentials belong to, or None; the username is its name."""
    dropzone = Dropzone.objects.filter(
        upload_method=method, enabled=True, name=name
    ).first()
    if dropzone is not None and dropzone.secret_matches(secret):
        return dropzone
    return None


def store_session(dropzone_id, paths):
    """Feed a finished session's files into the upload pipeline as one upload."""
    dropzone = Dropzone.objects.get(pk=dropzone_id)
    # Neither protocol carries a validity form, so the dropzone's default applies:
    # "always" keeps both bounds open, everything else starts now.
    valid_from = (
        None
        if dropzone.default_validity == Dropzone.Validity.ALWAYS
        else timezone.now()
    )
    streams = [path.open("rb") for path in paths]
    try:
        files = [File(stream, name=path.name) for path, stream in zip(paths, streams)]
        return process_upload(dropzone, files, valid_from=valid_from)
    finally:
        for stream in streams:
            stream.close()


def stored_file_count(dropzone_id, paths):
    """store_session, reduced to the file count the caller reports.

    The count is a query of its own, so it has to happen inside the same ``fresh`` call:
    asking afterwards would open a second connection on the calling thread that nothing
    closes again. None when the session wrote no files at all.
    """
    if not paths:
        return None
    return store_session(dropzone_id, paths).files.count()
