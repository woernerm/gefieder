"""Keeping the deploying branch clean by moving a first edit onto one of its own.

``main`` is the one branch crudman deploys. Disabling saves would lose work, and Lab saves
through checkpoints and autosave as well as the button. So the first save while on
``main`` creates a branch and lands there; the git panel shows where.
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

    Named after the file, which its author recognises in the branch list a week later.

    Args:
        path: The file being saved.

    Returns:
        The file's stem, reduced to what git and a reader both accept.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    return slug or FALLBACK


def _unique(name: str, root: Path) -> str:
    """The first unused branch name from ``name``, counting up.

    A second sitting on the same model must neither fail nor continue the first branch,
    which may be finished and pushed.

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

    Called from the contents manager for every save. Failures are swallowed: the save then
    goes to ``main``, which is untidy, whereas raising would lose the person's work. Reaching
    the origin still takes a deliberate push.

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
