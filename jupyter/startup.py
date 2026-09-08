"""What every kernel in this system has already done before the first cell runs.

Run by ``exec_files`` in the IPython configuration (see ``jupyter/ipython_kernel_config.py``). It
loads SQLMesh's magics and this system's cell routing, opens the project the notebook lives
in, and says which account and environment the session is working as -- so a new notebook is
immediately useful and nobody has to remember an incantation to make ``%evaluate`` work.

Failures here are printed rather than raised: a kernel that starts is one a person can fix
things from, and the most likely cause is a database that is still coming up.
"""
import os
from pathlib import Path


def _project() -> Path | None:
    """The SQLMesh project this kernel belongs to.

    A kernel started from the launcher rather than from a file has the person's home as its
    working directory, so the search starts at the server's root -- the workspace the
    spawner opened Lab on -- and only then at the working directory. That is what makes
    ``%evaluate`` work in a notebook created anywhere, which is most of them.

    Returns:
        The directory holding config.py, or None when there is no project to be found.
    """
    root = os.environ.get("JUPYTERHUB_ROOT_DIR") or ""
    for start in (Path(root) if root else None, Path.cwd()):
        if start is None:
            continue
        for directory in [start, *start.parents]:
            if (directory / "config.py").exists():
                return directory
    return None


def _load() -> None:
    """Load the extensions and open the project."""
    from sqlmesh.magics import register_magics

    ipython = get_ipython()  # noqa: F821 -- IPython provides this in the namespace.
    # Called rather than loaded as an extension: SQLMesh registers no IPython extension
    # hook, and its own automatic registration on import only fires in an environment it
    # recognises as a notebook.
    register_magics()
    ipython.run_line_magic("load_ext", "sqlnotebook.kernel")

    project = _project()
    if project is None:
        return

    ipython.run_line_magic("context", str(project))
    context = ipython.user_ns.get("context")
    if context is None:
        return

    # Which account and which environment, because both are answers a person otherwise has
    # to go and look up, and getting the second one wrong means planning over production.
    print(
        f"SQLMesh project {project.name} as {os.environ.get('SQLMESH_USER', 'unknown')}, "
        f"target environment {context.config.default_target_environment}."
    )


try:
    _load()
except Exception as error:  # noqa: BLE001 -- a kernel that starts can be debugged in.
    print(f"SQLMesh could not be loaded: {error}")
