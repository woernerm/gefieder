"""Which SQLMesh project a kernel belongs to, from where it was started."""
import os
from pathlib import Path

PROJECT = "sqlmesh"
"""The SQLMesh project inside the models repository; jupyter/spawn.py spells it too."""


def find_project() -> Path | None:
    """The SQLMesh project this kernel belongs to.

    Searched from the working directory upwards, then from the server's root -- the
    workspace the spawner opened Lab on. That is what makes ``%evaluate`` work in a
    notebook created anywhere, which is most of them.

    Returns:
        The directory holding config.py, or None when there is no project to be found.
    """
    root = os.environ.get("JUPYTERHUB_ROOT_DIR") or ""
    for start in (Path.cwd(), Path(root) if root else None):
        if start is None:
            continue
        for directory in [start, *start.parents]:
            if (directory / "config.py").exists():
                return directory
        # The server is rooted at the repository so the git panel can see .git, and the
        # project is one level in -- so a kernel started from the launcher, whose working
        # directory is the person's home, finds it here rather than by walking upwards.
        candidate = start / PROJECT
        if (candidate / "config.py").exists():
            return candidate
    return None
