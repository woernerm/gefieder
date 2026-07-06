"""The dropzones SFTP endpoint (run by ``manage.py sftpserver``).

The application runs its own SFTP server instead of watching a directory, so an uploader
needs no convention at all — no marker files, no manifest, no rename dance: connect
with the dropzone's name and secret, ``put`` one or more files, disconnect. The
server sees the transfer itself, so completeness needs no signalling: everything the
client fully transferred in one SFTP (or scp) session becomes one upload through the
same pipeline (``services.process_upload``) the browser and API uploads use. A
session that breaks off in the middle of a file stores nothing, like an aborted POST.

Every session is chrooted into its own throwaway directory, so an uploader only ever
sees the files of their own running session, never stored uploads or other dropzones'
data.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

import asyncssh
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files import File
from django.db import close_old_connections
from django.utils import timezone

from .models import Dropzone
from .services import UploadError, process_upload

logger = logging.getLogger(__name__)

# The dropzone identity of every currently connected client, keyed by its SSH
# connection: written on successful authentication, read by the SFTP sessions opened
# on the connection, removed when the connection closes.
_sessions = {}

# Keeps the fire-and-forget processing tasks alive until they finish; asyncio holds
# tasks only weakly, so an otherwise unreferenced task could vanish mid-run.
_tasks = set()


def _fresh(func, *args):
    """Run a database-facing function on a healthy connection.

    The server runs for weeks, so a connection the database dropped in the meantime
    (restart, idle timeout) is discarded before the call instead of failing it.
    """
    close_old_connections()
    return func(*args)


def _authenticate(username, password):
    """The dropzone the credentials belong to, or None; the username is its name."""
    dropzone = Dropzone.objects.filter(
        upload_method=Dropzone.Method.SFTP, enabled=True, name=username
    ).first()
    if dropzone is not None and dropzone.sftp_secret_matches(password):
        return dropzone
    return None


def _stored_file_count(dropzone_id, directory):
    """_store_session, reduced to the file count the log line needs; the count is a
    query too, and the ORM must not be touched from the event loop."""
    upload = _store_session(dropzone_id, directory)
    return upload.files.count() if upload is not None else None


def _store_session(dropzone_id, directory):
    """Feed a finished session's files into the upload pipeline as one upload.

    Returns the Upload, or None when the session wrote no files. Subdirectories are
    flattened: the pipeline stores bare file names anyway, and how a client arranged
    its temporary tree carries no meaning.
    """
    paths = sorted(p for p in directory.rglob("*") if p.is_file())
    if not paths:
        return None
    dropzone = Dropzone.objects.get(pk=dropzone_id)
    # SFTP carries no validity form, so the dropzone's default applies: "always"
    # keeps both bounds open, everything else is "from now on until replacement".
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


async def _finish_session(name, dropzone_id, directory, peer):
    """Store a finished session and clean up its directory afterwards."""
    try:
        stored = await sync_to_async(_fresh)(_stored_file_count, dropzone_id, directory)
    except UploadError as error:
        # The checker/converter verdict. The uploader has already disconnected, so
        # the rejection can only be logged here, not shown to them.
        logger.warning("Dropzone '%s': upload from %s rejected: %s", name, peer, error)
    except Exception:
        logger.exception("Dropzone '%s': storing the upload from %s failed", name, peer)
    else:
        if stored is None:
            logger.info("Dropzone '%s': session from %s ended without files", name, peer)
        else:
            logger.info(
                "Dropzone '%s': upload from %s accepted, %d file(s) stored",
                name,
                peer,
                stored,
            )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class SFTPEndpoint(asyncssh.SSHServer):
    """One instance per SSH connection: authenticates a dropzone for the SFTP
    sessions opened on the connection (see :class:`SessionSFTPServer`)."""

    def connection_made(self, conn):
        self._conn = conn
        peer = conn.get_extra_info("peername")
        self._peer = peer[0] if peer else "unknown"

    def begin_auth(self, username):
        return True  # never allow a login without credentials

    def password_auth_supported(self):
        return True

    async def validate_password(self, username, password):
        dropzone = await sync_to_async(_fresh)(_authenticate, username, password)
        if dropzone is None:
            logger.warning("Rejected SFTP login %r from %s", username, self._peer)
            return False
        _sessions[self._conn] = (dropzone.name, dropzone.pk)
        logger.info("Dropzone '%s': login from %s", dropzone.name, self._peer)
        return True

    def connection_lost(self, exc):
        _sessions.pop(self._conn, None)


class SessionSFTPServer(asyncssh.SFTPServer):
    """One SFTP (or scp) session: a chrooted throwaway directory that turns into one
    upload when the session ends.

    The commit decision is made here, at the session level, rather than at the SSH
    connection level: well-behaved clients close every file and the SFTP channel but
    not all of them disconnect cleanly afterwards (paramiko, for one, just drops the
    socket), and an upload must not be lost to that. asyncssh force-closes the
    handles a client never closed during its cleanup — while the channel reports
    ``is_closing()`` — and then calls :meth:`exit` exactly once, so a forced close
    marks the transfer as broken off mid-file, and everything else is complete.
    """

    def __init__(self, chan):
        conn = chan.get_connection()
        self._name, self._dropzone_id = _sessions[conn]
        peer = conn.get_extra_info("peername")
        self._peer = peer[0] if peer else "unknown"
        self._directory = Path(tempfile.mkdtemp(prefix="dropzone-session-"))
        self._incomplete = False
        super().__init__(chan, chroot=self._directory)

    def close(self, file_obj):
        # A close while the channel is already closing is asyncssh's cleanup of a
        # file the client never finished: the session broke off mid-file.
        if self.channel.is_closing():
            self._incomplete = True
        super().close(file_obj)

    def exit(self):
        if self._incomplete:
            # Nothing is stored, like an aborted POST; the uploader's client saw the
            # broken transfer on its side and retries the whole session.
            logger.warning(
                "Dropzone '%s': session from %s broke off mid-file, nothing stored",
                self._name,
                self._peer,
            )
            shutil.rmtree(self._directory, ignore_errors=True)
            return
        task = asyncio.get_running_loop().create_task(
            _finish_session(self._name, self._dropzone_id, self._directory, self._peer)
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


def _host_key():
    """The server's persistent host key, generated on first start.

    Kept under SFTP_DIR (the sftp volume in the deployment) so the server identity
    survives restarts and updates; a changed host key would make every SFTP client
    refuse to reconnect.
    """
    path = Path(settings.SFTP_DIR) / "ssh_host_ed25519_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        asyncssh.generate_private_key("ssh-ed25519").write_private_key(path)
        path.chmod(0o600)
    return path


async def serve(port):
    """Listen forever: one endpoint per connection, one chroot per session.

    ``allow_scp`` accepts uploads from scp clients through the same chroot, so both
    of the two commands everyone has installed just work.
    """
    server = await asyncssh.listen(
        host="",
        port=port,
        server_host_keys=[str(_host_key())],
        server_factory=SFTPEndpoint,
        sftp_factory=SessionSFTPServer,
        allow_scp=True,
    )
    logger.info("Dropzones SFTP endpoint listening on port %d", port)
    await server.wait_closed()
