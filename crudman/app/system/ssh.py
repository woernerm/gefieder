"""The models repository over SSH, for a clone on someone's own machine.

Served by the dropzones' SSH endpoint (``dropzones/sftp.py``) on the port it already
publishes, so a laptop needs git and a key and nothing else. A dropzone logs in with a
password under its own name; a person logs in as ``repo.SSH_USER`` with a key they added
on the SSH keys page, and may then run the two commands git itself sends -- never a shell.
"""

import asyncio
import logging
import shlex

import asyncssh

from dbusers.utils import db_role_for_user, signed_in_recently
from notebooks.utils import EDITOR_GROUPS

from . import repo
from .models import SSHKey

logger = logging.getLogger(__name__)

COMMANDS = {"git-upload-pack": "upload-pack", "git-receive-pack": "receive-pack"}
"""What a fetch and a push run on the server, and the git subcommand each becomes."""

def parse_key(text: str) -> tuple[str, str, str]:
    """Read a public key as someone pastes it out of their ``.pub`` file.

    Args:
        text: The pasted key.

    Returns:
        The key as one OpenSSH line, its fingerprint, and the comment it carries.

    Raises:
        ValueError: The text is not a public key, in words the person can act on.
    """
    # asyncssh would read the public half out of a private key, and accepting one quietly
    # would leave the person believing nothing went wrong.
    if "PRIVATE KEY" in text:
        raise ValueError(
            "That is a private key, which must never leave your machine. Paste the file "
            "ending in .pub instead, and consider replacing the key you pasted."
        )
    try:
        key = asyncssh.import_public_key(text.strip())
    except (asyncssh.KeyImportError, ValueError):
        raise ValueError(
            "That is not an SSH public key. Paste the whole line from the file ending in "
            ".pub, for example ~/.ssh/id_ed25519.pub."
        ) from None
    line = key.export_public_key("openssh").decode().strip()
    return line, key.get_fingerprint(), key.get_comment() or ""


def key_owner(fingerprint: str) -> str | None:
    """Who may reach the repository with this key, asked afresh at every connection.

    Args:
        fingerprint: The SHA256 fingerprint of the key the client authenticates with.

    Returns:
        The owner's username when they are active, signed in recently and hold the editor
        rank upwards -- the same line the notebooks draw, since pushing is deploying -- and
        None otherwise.
    """
    key = SSHKey.objects.select_related("user").filter(fingerprint=fingerprint).first()
    if key is None or not key.user.is_active or not signed_in_recently(key.user):
        return None
    return key.user.username if db_role_for_user(key.user) in EDITOR_GROUPS else None


async def _pipe(read, write, drain, close=None) -> None:
    """Copy one stream into another until the first ends."""
    try:
        while data := await read(65536):
            write(data)
            await drain()
    except (BrokenPipeError, ConnectionError):
        # The other side went away; git reports what that meant through its exit status.
        pass
    if close:
        close()


async def serve(process: asyncssh.SSHServerProcess, username: str) -> None:
    """Run the git command a client asked for against the origin.

    Args:
        process: The SSH session, carrying the command and the client's streams.
        username: Who authenticated, for the log.
    """
    try:
        command, path = shlex.split(process.command or "")
    except ValueError:
        command = path = ""
    path = path.strip("/").removeprefix("~/")
    if command not in COMMANDS or path != repo.SSH_REPOSITORY:
        process.stderr.write(
            f"This address serves git alone: git clone {repo.clone_url()}\n".encode()
        )
        process.exit(1)
        return
    if not repo.is_local():
        process.stderr.write(f"The models repository is {repo.ORIGIN}\n".encode())
        process.exit(1)
        return

    # A push may add to the history but not rewrite or delete it: production is meant to
    # correspond to a commit somebody can still point at.
    git = await asyncio.create_subprocess_exec(
        "git",
        "-c", "receive.denyNonFastForwards=true",
        "-c", "receive.denyDeletes=true",
        COMMANDS[command],
        repo.ORIGIN,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    feed = asyncio.create_task(
        _pipe(process.stdin.read, git.stdin.write, git.stdin.drain, git.stdin.close)
    )
    try:
        await asyncio.gather(
            _pipe(git.stdout.read, process.stdout.write, process.stdout.drain),
            _pipe(git.stderr.read, process.stderr.write, process.stderr.drain),
        )
        status = await git.wait()
    finally:
        feed.cancel()
        if git.returncode is None:
            git.kill()
    action = "push" if command == "git-receive-pack" else "fetch"
    logger.info("Models repository: %s by %s, exit %d", action, username, status)
    process.exit(status)
