"""The dropzones Arrow Flight endpoint (run by ``manage.py flightserver``).

Arrow Flight sends tables, not files, so every table is written to one parquet file and
the set handed to the same pipeline the other methods use. A client names each table in
the flight descriptor (``<dropzone>/<table>``).

One upload spans several ``DoPut`` calls, tied together by the bearer token minted during
authentication, and ends with a ``commit`` action. The commit is explicit rather than
inferred from the disconnect because a client killed mid-transfer looks exactly like one
that finished. ``ServerCallContext.is_cancelled`` tells the two apart, so it guards every
table and a session that saw a truncated one refuses to commit.
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

from . import endpoint
from .models import Dropzone
from .services import UploadError

logger = logging.getLogger(__name__)

# The open upload sessions, keyed by the bearer token minted at authentication. _lock
# because Flight serves calls on a thread pool, so two DoPuts can run at once.
_sessions = {}
_lock = threading.Lock()


class Session:
    """The tables collected for one upload, and the dropzone they belong to.

    Attributes:
        dropzone_id: Primary key of the dropzone being uploaded to.
        name: The dropzone's name, for log messages.
        directory: Throwaway directory holding the parquet files received so far.
        touched: Monotonic timestamp of the last call, used by the sweeper.
        truncated: Whether a table broke off mid-transfer, which blocks the commit.
    """

    def __init__(self, dropzone_id, name):
        self.dropzone_id = dropzone_id
        self.name = name
        self.directory = Path(tempfile.mkdtemp(prefix="dropzone-flight-"))
        self.touched = time.monotonic()
        self.truncated = False

    def discard(self):
        shutil.rmtree(self.directory, ignore_errors=True)


class AuthMiddleware(flight.ServerMiddleware):
    """Carries the session token of the call it belongs to.

    ``sending_headers`` hands a freshly minted token back to the client, which presents it
    as a bearer token on the rest of the upload.

    Attributes:
        token: The session token of this call's upload.
    """

    def __init__(self, token):
        self.token = token

    def sending_headers(self):
        return {"authorization": f"Bearer {self.token}"}


class AuthMiddlewareFactory(flight.ServerMiddlewareFactory):
    """Authenticates every call and opens the upload session on the first one.

    ``Basic`` credentials open a session and mint its token; every later call of the same
    upload presents that token as ``Bearer``.
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
        # decoder rejects.
        import base64

        try:
            decoded = base64.b64decode(credentials + "===").decode()
        except Exception:
            raise flight.FlightUnauthenticatedError("Malformed credentials.") from None
        name, _, secret = decoded.partition(":")
        dropzone = endpoint.fresh(
            endpoint.authenticate, Dropzone.Method.FLIGHT, name, secret
        )
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
        """Receive one table and write it to the session's directory as parquet.

        Args:
            context: The Flight call context.
            descriptor: Flight descriptor whose last path element names the table.
            reader: Record batch reader carrying the table.
            writer: Unused; the endpoint sends nothing back on a put.

        Raises:
            FlightServerError: The table broke off mid-transfer.
        """
        _, session = self._session(context)
        # The descriptor path is <dropzone>/<table>; the bare table name keeps a client
        # from writing outside the session directory.
        table_name = Path(descriptor.path[-1].decode()).name
        table = reader.read_all()
        if context.is_cancelled():
            # The client vanished mid-table, which ends the stream without an error, so
            # only this flag tells a truncated table from a whole one.
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
        """``commit`` stores the collected tables as one upload and ends the session.

        Args:
            context: The Flight call context.
            action: The requested action; only ``commit`` is supported.

        Returns:
            An iterator over one Result naming the number of files stored.

        Raises:
            FlightServerError: Unknown action, nothing to store, a truncated table, or
                a rejection by the dropzone's checker or converter.
        """
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
                count = endpoint.fresh(
                    endpoint.stored_file_count,
                    session.dropzone_id,
                    sorted(p for p in session.directory.iterdir() if p.is_file()),
                )
            except UploadError as error:
                # Unlike SFTP the uploader is still connected, so the checker's verdict
                # reaches them instead of only the log.
                logger.warning(
                    "Dropzone '%s': upload rejected: %s", session.name, error
                )
                raise flight.FlightServerError(str(error)) from error
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

    Nothing is stored: committing one would turn a client crash into a partial upload.

    Args:
        timeout: Seconds of inactivity after which a session is abandoned.
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
    """Listen forever, sweeping abandoned upload sessions as we go.

    Args:
        port: TCP port to listen on.
        timeout: Seconds of inactivity before a session is swept. Defaults to
            ``settings.FLIGHT_SESSION_TIMEOUT``.
    """
    timeout = settings.FLIGHT_SESSION_TIMEOUT if timeout is None else timeout
    server = FlightEndpoint(
        location=f"grpc://0.0.0.0:{port}",
        # The handshake authenticate_basic_token performs is offered only with an auth
        # handler installed; the middleware does the checking, on every call.
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
