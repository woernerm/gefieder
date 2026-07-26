"""The dropzones Arrow Flight endpoint (run by ``manage.py flightserver``).

Arrow Flight sends tables, not files, so this endpoint writes every table it receives
to one parquet file and hands the set to the same pipeline (``services.process_upload``)
the browser, API and SFTP uploads use. A client names each table in the flight
descriptor (``<dropzone>/<table>``), so ``issues`` arrives as ``issues.parquet``.

One upload consists of several ``DoPut`` calls, so the endpoint has to know which of
them belong together. The bearer token minted during authentication serves as that
upload identity: it is unguessable, bound to the authenticated dropzone and stable for
the life of the client's connection, so no separate session handshake is needed. The
client closes the upload with a ``commit`` action, which is what turns the collected
tables into one Upload — a client that dies beforehand stores nothing at all, like an
aborted POST.

The commit is explicit rather than inferred from the disconnect because a client killed
mid-transfer looks exactly like one that finished: the socket closes and the record
batch stream simply ends. ``ServerCallContext.is_cancelled`` is what tells the two
apart, so it guards every table; a session that saw a truncated table is poisoned and
refuses to commit.
"""

import logging
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pyarrow.flight as flight
import pyarrow.parquet as parquet
from django.conf import settings
from django.core.files import File
from django.db import close_old_connections
from django.utils import timezone

from .models import Dropzone
from .services import UploadError, process_upload

logger = logging.getLogger(__name__)

# The open upload sessions, keyed by the bearer token minted at authentication. Guarded
# by _lock because Flight serves calls on a thread pool, so two DoPuts of one upload can
# run at the same time.
_sessions = {}
_lock = threading.Lock()


class Session:
    """The tables collected for one upload, and the dropzone they belong to."""

    def __init__(self, dropzone_id, name):
        self.dropzone_id = dropzone_id
        self.name = name
        self.directory = Path(tempfile.mkdtemp(prefix="dropzone-flight-"))
        self.touched = time.monotonic()
        # Set when a table broke off mid-transfer: the upload can no longer be complete,
        # so the commit refuses rather than storing a truncated table.
        self.truncated = False

    def discard(self):
        shutil.rmtree(self.directory, ignore_errors=True)


def _fresh(func, *args):
    """Run a database-facing function on a healthy connection.

    The server runs for weeks, so a connection the database dropped in the meantime
    (restart, idle timeout) is discarded before the call instead of failing it.
    """
    close_old_connections()
    return func(*args)


def _authenticate(name, secret):
    """The dropzone the credentials belong to, or None; the username is its name."""
    dropzone = Dropzone.objects.filter(
        upload_method=Dropzone.Method.FLIGHT, enabled=True, name=name
    ).first()
    if dropzone is not None and dropzone.flight_secret_matches(secret):
        return dropzone
    return None


def _store_session(dropzone_id, directory):
    """Feed a committed session's parquet files into the pipeline as one upload."""
    paths = sorted(p for p in directory.iterdir() if p.is_file())
    dropzone = Dropzone.objects.get(pk=dropzone_id)
    # Flight carries no validity form, so the dropzone's default applies, exactly as
    # for SFTP: "always" keeps both bounds open, everything else starts now.
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


class AuthMiddleware(flight.ServerMiddleware):
    """Carries the session token of the call it belongs to.

    ``sending_headers`` is what hands a freshly minted token back to the client, which
    then presents it as a bearer token on the rest of the upload.
    """

    def __init__(self, token):
        self.token = token

    def sending_headers(self):
        return {"authorization": f"Bearer {self.token}"}


class AuthMiddlewareFactory(flight.ServerMiddlewareFactory):
    """Authenticates every call and opens the upload session on the first one.

    ``Basic`` credentials (the dropzone's name and secret) open a session and mint its
    token; every later call of the same upload presents that token as ``Bearer``.
    """

    def start_call(self, info, headers):
        header = next(
            (headers[key][0] for key in headers if key.lower() == "authorization"), None
        )
        if not header:
            raise flight.FlightUnauthenticatedError("No credentials.")
        scheme, _, value = header.partition(" ")
        if scheme == "Basic":
            return AuthMiddleware(self._open_session(value))
        if scheme == "Bearer":
            with _lock:
                if value not in _sessions:
                    raise flight.FlightUnauthenticatedError(
                        "Unknown or expired upload token."
                    )
            return AuthMiddleware(value)
        raise flight.FlightUnauthenticatedError(f"Unsupported scheme '{scheme}'.")

    def _open_session(self, credentials):
        # pyarrow's authenticate_basic_token sends unpadded base64, which the standard
        # decoder rejects; the padding is re-added rather than decoded leniently.
        import base64

        try:
            decoded = base64.b64decode(credentials + "===").decode()
        except Exception:
            raise flight.FlightUnauthenticatedError("Malformed credentials.") from None
        name, _, secret = decoded.partition(":")
        dropzone = _fresh(_authenticate, name, secret)
        if dropzone is None:
            logger.warning("Rejected Arrow Flight login %r", name)
            raise flight.FlightUnauthenticatedError("Invalid credentials.")
        token = secrets.token_urlsafe(32)
        with _lock:
            _sessions[token] = Session(dropzone.pk, dropzone.name)
        logger.info("Dropzone '%s': Arrow Flight upload started", dropzone.name)
        return token


class FlightEndpoint(flight.FlightServerBase):
    """Collects the tables of one upload and stores them when the client commits."""

    def _session(self, context):
        token = context.get_middleware("auth").token
        with _lock:
            session = _sessions.get(token)
        if session is None:
            raise flight.FlightUnauthenticatedError("Unknown or expired upload token.")
        session.touched = time.monotonic()
        return token, session

    def do_put(self, context, descriptor, reader, writer):
        """Receive one table and write it to the session's directory as parquet."""
        _, session = self._session(context)
        # The descriptor path is <dropzone>/<table>; only the table name matters here,
        # and its bare form keeps a client from writing outside the session directory.
        table_name = Path(descriptor.path[-1].decode()).name
        table = reader.read_all()
        if context.is_cancelled():
            # The client vanished mid-table. The stream ends without an error in that
            # case, so only this flag distinguishes a truncated table from a whole one.
            session.truncated = True
            logger.warning(
                "Dropzone '%s': table '%s' broke off mid-transfer",
                session.name,
                table_name,
            )
            raise flight.FlightServerError(
                f"The table '{table_name}' broke off mid-transfer."
            )
        parquet.write_table(table, session.directory / f"{table_name}.parquet")

    def do_action(self, context, action):
        """``commit`` stores the collected tables as one upload and ends the session."""
        token, session = self._session(context)
        if action.type != "commit":
            raise flight.FlightServerError(f"Unknown action '{action.type}'.")
        with _lock:
            _sessions.pop(token, None)
        try:
            if session.truncated:
                raise flight.FlightServerError(
                    "A table broke off mid-transfer; nothing was stored."
                )
            if not any(session.directory.iterdir()):
                raise flight.FlightServerError("The upload contains no tables.")
            try:
                upload = _fresh(_store_session, session.dropzone_id, session.directory)
            except UploadError as error:
                # The checker/converter verdict; unlike SFTP the uploader is still
                # connected, so the rejection reaches them instead of only the log.
                logger.warning(
                    "Dropzone '%s': upload rejected: %s", session.name, error
                )
                raise flight.FlightServerError(str(error)) from error
            count = upload.files.count()
            logger.info(
                "Dropzone '%s': upload accepted, %d file(s) stored", session.name, count
            )
            return iter([flight.Result(f"stored {count} file(s)".encode())])
        finally:
            session.discard()

    def list_actions(self, context):
        return [("commit", "Store the tables sent so far as one upload.")]


def _sweep(timeout):
    """Discard sessions whose client never committed.

    Nothing is stored for them: an upload that was never committed is as incomplete as
    an SFTP session that broke off mid-file, and committing it would turn a client
    crash into a silently partial upload.
    """
    with _lock:
        expired = [
            token
            for token, session in _sessions.items()
            if time.monotonic() - session.touched > timeout
        ]
        sessions = [_sessions.pop(token) for token in expired]
    for session in sessions:
        logger.warning(
            "Dropzone '%s': upload abandoned without a commit, nothing stored",
            session.name,
        )
        session.discard()


def serve(port, timeout=None):
    """Listen forever, sweeping abandoned upload sessions as we go."""
    timeout = settings.FLIGHT_SESSION_TIMEOUT if timeout is None else timeout
    server = FlightEndpoint(
        location=f"grpc://0.0.0.0:{port}",
        # The handshake pyarrow's authenticate_basic_token performs is only offered
        # when an auth handler is installed; the credentials themselves are checked by
        # the middleware, which sees every later call too.
        auth_handler=NoOpAuthHandler(),
        middleware={"auth": AuthMiddlewareFactory()},
    )
    stop = threading.Event()

    def sweeper():
        while not stop.wait(min(timeout, 60)):
            _sweep(timeout)

    thread = threading.Thread(target=sweeper, daemon=True)
    thread.start()
    logger.info("Dropzones Arrow Flight endpoint listening on port %d", port)
    try:
        server.serve()
    finally:
        stop.set()


class NoOpAuthHandler(flight.ServerAuthHandler):
    """Enables the basic-token handshake; the middleware does the actual checking."""

    def authenticate(self, outgoing, incoming):
        pass

    def is_valid(self, token):
        return ""
