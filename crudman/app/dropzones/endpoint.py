"""Database work shared by the SFTP and Arrow Flight endpoints.

Both are long-lived server processes outside the request cycle, authenticating against a
dropzone rather than a Django session. Only the protocol differs between them.
"""

from django.core.files import File
from django.db import close_old_connections, connections

from .models import Dropzone
from .services import process_upload


def fresh(func, *args):
    """Run a database-facing function on a healthy connection, and leave none behind.

    The servers run for weeks, so a connection dropped in the meantime is discarded
    before the call rather than failing it. Both answer on worker threads, where nothing
    signals the end of a call, so a connection opened there is held until the process
    exits.

    Args:
        func: The database-facing function to call.
        *args: Positional arguments passed on to func.

    Returns:
        Whatever func returns.
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
    streams = [path.open("rb") for path in paths]
    try:
        files = [File(stream, name=path.name) for path, stream in zip(paths, streams)]
        # Neither protocol carries a validity form, so the dropzone's default applies.
        return process_upload(
            dropzone, files, valid_from=dropzone.default_valid_from()
        )
    finally:
        for stream in streams:
            stream.close()


def stored_file_count(dropzone_id, paths):
    """store_session, reduced to the file count the caller reports.

    Counting is a query of its own and belongs in the same ``fresh`` call; asking
    afterwards would open a second connection that nothing closes.

    Args:
        dropzone_id: Primary key of the dropzone that was uploaded to.
        paths: The files the session wrote.

    Returns:
        The number of files stored, or None if the session wrote no files.
    """
    if not paths:
        return None
    return store_session(dropzone_id, paths).files.count()
