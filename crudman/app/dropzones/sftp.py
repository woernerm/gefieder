"""The dropzones SFTP endpoint (run by ``manage.py sftpserver``).

Running the server rather than watching a directory means an uploader needs no
convention: connect with the dropzone's name and secret, ``put`` files, disconnect. The
server sees the transfer, so completeness needs no signalling -- one session becomes one
upload, and a session that breaks off mid-file stores nothing.

Every session is chrooted into its own throwaway directory.
"""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

import asyncssh
from asgiref.sync import sync_to_async
from django.conf import settings

from . import endpoint
from .models import Dropzone
from .services import UploadError

logger = logging.getLogger(__name__)

# The dropzone identity of every connected client, keyed by its SSH connection: written
# at authentication, read by the sessions on that connection, removed when it closes.
_sessions = {}

# asyncio holds tasks only weakly, so an unreferenced one could vanish mid-run.
_tasks = set()


def _session_files(directory):
    """The files a finished session wrote, flattened.

    Args:
        directory: The session's chroot directory.

    Returns:
        Every file below it, sorted. Subdirectories are not preserved: the pipeline
        stores bare file names.
    """
    return sorted(p for p in directory.rglob("*") if p.is_file())


def _store_session_files(dropzone_id, directory):
    """One worker-thread call: list what the session wrote and store it."""
    return endpoint.fresh(
        endpoint.stored_file_count, dropzone_id, _session_files(directory)
    )


async def _finish_session(name, dropzone_id, directory, peer):
    """Store a finished session and clean up its directory afterwards."""
    try:
        # Listing runs on the worker thread too: filesystem work on the event loop would
        # stall every other connection.
        stored = await sync_to_async(_store_session_files)(dropzone_id, directory)
    except UploadError as error:
        # The uploader has already disconnected, so the checker's verdict can only be
        # logged.
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
    """One instance per SSH connection.

    Authenticates a dropzone for the sessions :class:`SessionSFTPServer` then serves.
    """

    def connection_made(self, conn):
        self._conn = conn
        peer = conn.get_extra_info("peername")
        self._peer = peer[0] if peer else "unknown"

    def begin_auth(self, username):
        return True  # never allow a login without credentials

    def password_auth_supported(self):
        return True

    async def validate_password(self, username, password):
        dropzone = await sync_to_async(endpoint.fresh)(
            endpoint.authenticate, Dropzone.Method.SFTP, username, password
        )
        if dropzone is None:
            logger.warning("Rejected SFTP login %r from %s", username, self._peer)
            return False
        _sessions[self._conn] = (dropzone.name, dropzone.pk)
        logger.info("Dropzone '%s': login from %s", dropzone.name, self._peer)
        return True

    def connection_lost(self, exc):
        _sessions.pop(self._conn, None)


class SessionSFTPServer(asyncssh.SFTPServer):
    """One SFTP (or scp) session, stored as one upload when the session ends.

    The commit decision is made per session rather than per connection: not every client
    disconnects cleanly after closing its files (paramiko drops the socket), and an upload
    must not be lost to that. asyncssh force-closes handles the client left open while the
    channel reports ``is_closing()``, which is what marks a transfer broken off mid-file.
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
        # A close while the channel is already closing is asyncssh cleaning up a file
        # the client never finished.
        if self.channel.is_closing():
            self._incomplete = True
        super().close(file_obj)

    def exit(self):
        if self._incomplete:
            # Nothing is stored, like an aborted POST; the client saw the broken
            # transfer and retries the whole session.
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

    Returns:
        Path to the key under SFTP_DIR, so the server identity survives restarts; a
        changed host key would make every SFTP client refuse to reconnect.
    """
    path = Path(settings.SFTP_DIR) / "ssh_host_ed25519_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        asyncssh.generate_private_key("ssh-ed25519").write_private_key(path)
        path.chmod(0o600)
    return path


async def serve(port):
    """Listen forever: one endpoint per connection, one chroot per session.

    ``allow_scp`` accepts uploads from scp clients through the same chroot.

    Args:
        port: TCP port to listen on.
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
