"""Keeping the deploying branch clean by moving a first edit onto one of its own.

``main`` is the one branch crudman deploys (``crudman/app/system/repo.py``), so work that
is still being written must not sit on it. The lever could have been a disabled Save
button, but a person with unsaved edits and no way to save them has lost work, and Lab
saves through checkpoints and autosave as well as through the button -- several doors to
bolt, each of which strands the same person.

So nothing is forbidden. The first save while on ``main`` creates a branch and lands there,
and every later save is an ordinary save on the branch the person is already on. The git
panel shows where they ended up, which teaches the arrangement better than a modal, and
their workspace holds the branch and its uncommitted edits until they come back to it.
"""
import os
import re
import subprocess
from pathlib import Path

MAIN = "main"
"""The branch that deploys, and therefore the one a save moves off.

Spelled as ``crudman/app/system/repo.py`` spells it. A person who checked out some other
branch is left alone: they chose it, and only ``main`` carries the deployment.
"""

PREFIX = "work"
"""What every branch created here is named under, so ``git branch`` sorts the unfinished
work together and a push cannot collide with a release branch."""

FALLBACK = "models"
"""The name for a branch whose first save was a file with nothing to name it after."""


def _git(*arguments: str, root: Path) -> str:
    """Run git in a workspace and return its output.

    Args:
        *arguments: The command and its arguments.
        root: The working tree to run in.

    Returns:
        The standard output, stripped.

    Raises:
        subprocess.CalledProcessError: When git fails.
    """
    return subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(path: Path) -> Path | None:
    """The root of the working tree a file belongs to.

    Args:
        path: The file being saved, as an absolute path.

    Returns:
        The directory holding ``.git``, or None when the file is outside a repository --
        a notebook in the person's home, which is theirs and not the models'.
    """
    for directory in path.parents:
        if (directory / ".git").exists():
            return directory
    return None


def _name_for(path: Path) -> str:
    """The branch name a first save of this file earns.

    Named after the file rather than the hour: a branch called ``silver-effort-rollup`` is
    one its author recognises in the panel's list a week later, and a timestamp is not.

    Args:
        path: The file being saved.

    Returns:
        The slug, which is the file's stem reduced to what git and a reader both accept.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    return slug or FALLBACK


def _unique(name: str, root: Path) -> str:
    """The first unused branch name from ``name``, counting up.

    Editing the same model in two sittings is ordinary, and the second must not fail on
    the first one's branch nor silently continue it -- the earlier work may be finished
    and pushed.

    Args:
        name: The wanted name.
        root: The working tree to look in.

    Returns:
        ``name``, or ``name-2``, ``name-3``, and so on.
    """
    taken = set(_git("branch", "--format=%(refname:short)", root=root).splitlines())
    if name not in taken:
        return name
    return next(f"{name}-{n}" for n in range(2, len(taken) + 3) if f"{name}-{n}" not in taken)


def switch_off_main(path: str | Path) -> None:
    """Move a workspace off ``main`` before a file in it is written.

    Called from the contents manager for every save, which is the one place every door
    into a file -- the button, autosave, a checkpoint, Save As -- passes through.

    Failures are swallowed on purpose. Every one of them means the save goes to ``main``
    instead of a branch, which is untidy; raising instead would mean the save does not
    happen at all, which loses the person's work. The workspace is a clone with no
    protected branch, so nothing downstream is at risk: deployment reads the origin, and
    reaching it still takes a deliberate push.

    Args:
        path: The file about to be written, as an absolute path.
    """
    root = _repository(Path(path))
    if root is None:
        return

    try:
        if _git("rev-parse", "--abbrev-ref", "HEAD", root=root) != MAIN:
            return

        user = os.environ.get("JUPYTERHUB_USER") or Path.home().name
        _git("checkout", "-b", _unique(f"{PREFIX}/{user}/{_name_for(Path(path))}", root=root),
             root=root)
    except (subprocess.CalledProcessError, OSError):
        pass
