"""What every kernel in this system has already done before the first cell runs.

Run by ``exec_files`` in the IPython configuration (see ``jupyter/ipython_kernel_config.py``). It
loads SQLMesh's magics and this system's cell routing, opens the project the notebook lives
in, and says which account and environment the session is working as -- so a new notebook is
immediately useful and nobody has to remember an incantation to make ``%evaluate`` work.

Failures here are printed rather than raised: a kernel that starts is one a person can fix
things from, and the most likely cause is a database that is still coming up.
"""
import os
import warnings
from pathlib import Path


WRAPPER = Path(os.environ.get("NOTEBOOK_VENV", "/opt/notebook"), "etc", "quaktheme",
               "widget.js")
"""The ES module wrapped around quak's own, installed beside this file by the Dockerfile.

Spelled from the environment rather than ``__file__``, which IPython's ``exec_files`` does
not define -- and that is how this file is run."""


def _silence_seed_warnings() -> None:
    """Drop the pandas deprecations SQLMesh emits while loading a seed model.

    ``sqlmesh.core.model.definition`` passes ``infer_datetime_format`` and
    ``errors="ignore"``, both deprecated in pandas 2; the first is now the default and
    ignored, so neither changes a result today. There is no release to upgrade to -- 0.236.2
    is current -- and the pair is printed around every rendered model, which is where a
    warning a person should read would appear too.

    Scoped to that module and those two messages: a future pandas will raise on the second
    rather than warn, and anything else SQLMesh warns about still reaches the notebook.
    """
    for message in ("infer_datetime_format", "errors='ignore' is deprecated"):
        warnings.filterwarnings(
            "ignore",
            message=f".*{message}.*",
            module=r"sqlmesh\.core\.model\.definition",
        )


def _themed_widget(quak):
    """quak's widget, with the two rules its shipped stylesheet puts out of reach.

    The table is drawn in a shadow root. Custom properties cross that boundary -- which is
    what ``~/.jupyter/custom/custom.css`` relies on -- but ordinary selectors do not, and
    quak exports no ``::part``. Its scroll height is written as an inline style, and the
    row under the pointer shares ``--light-silver`` with every border in the table; neither
    can be reached from a stylesheet outside.

    So the widget's ES module is composed here: quak's own bundle, its export rewritten to
    a fixed name, followed by a wrapper that delegates to it and adds a stylesheet to the
    same shadow root. ``_esm`` is an anywidget trait and the root is ``mode="open"``, so
    this uses what both publish rather than reaching past it.

    Args:
        quak: The imported module, whose Widget is subclassed and whose bundle is read.

    Returns:
        The Widget subclass to render a dataframe with.
    """
    import re

    # _esm is anywidget's FileContents, whose str() is the bundle itself.
    bundle = str(quak.Widget._esm)
    # Its last statement, naming the factory under whatever minified identifier this
    # release happens to use. Rewritten rather than matched, so no name is hardcoded.
    export = re.search(r"export\{([A-Za-z0-9_$]+) as default[^}]*\};?\s*$", bundle)
    if export is None or not WRAPPER.exists():
        # A quak whose bundle no longer ends that way: better an unthemed table than a
        # module that does not load at all.
        return quak.Widget

    composed = (
        bundle[: export.start()]
        + f"const quakFactory = {export.group(1)};\n"
        + WRAPPER.read_text()
    )

    class ThemedWidget(quak.Widget):
        """quak's widget with this system's stylesheet in its shadow root."""

        _esm = composed

    return ThemedWidget


def _explore_columns() -> None:
    """Render a dataframe as quak's column explorer: distributions above every column.

    ``%fetchdf`` and ``%evaluate`` hand back a dataframe, whose plain repr truncates to a
    few rows -- the wrong shape for looking at what a model produced. quak replaces
    IPython's display formatter, so no cell has to call anything.

    Its widget loads the table into a DuckDB of its own to compute the summaries, so it is
    pointed at the previews and results a person reads, not at whatever a cell happens to
    return: anything that is not a dataframe goes through unchanged.

    Left out when quak is not installed: it is an operator's entry in
    ``JUPYTER_EXTENSIONS``, so an image built without it must still start a kernel.
    """
    try:
        import quak
    except ImportError:
        return

    widget = _themed_widget(quak)

    def explorer(obj: object) -> object:
        """quak's own formatter, but only for what it can actually build a table from."""
        # A Series advertises the Arrow interface quak accepts and then fails to convert:
        # it is one column, not a struct. Left alone here, it renders as its own repr.
        import pandas as pd

        if isinstance(obj, pd.Series):
            return obj
        rendered = quak.default_formatter(obj)
        return widget(obj) if isinstance(rendered, quak.Widget) else rendered

    quak.set_formatter(explorer)

    ipython = get_ipython()  # noqa: F821 -- IPython provides this in the namespace.
    ipython.run_line_magic("load_ext", "quak")


PROJECT = "sqlmesh"
"""The SQLMesh project inside the models repository; jupyter/spawn.py spells it too."""


def _project() -> Path | None:
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


def _load() -> None:
    """Load the extensions and open the project."""
    _silence_seed_warnings()
    _explore_columns()

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
