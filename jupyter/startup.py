"""What every kernel in this system has already done before the first cell runs.

Run by ``exec_files`` (see ``jupyter/ipython_kernel_config.py``). Loads SQLMesh's magics
and this system's cell routing, opens the notebook's project, and names the account and
environment the session works as.

Failures are reported rather than raised: a kernel that starts is one a person can fix
things from, and the likely cause is a database still coming up.
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
    ``errors="ignore"``, both deprecated in pandas 2 without changing a result. Printed
    around every rendered model, they would bury warnings a person should read. Scoped to
    that module and those two messages.
    """
    for message in ("infer_datetime_format", "errors='ignore' is deprecated"):
        warnings.filterwarnings(
            "ignore",
            message=f".*{message}.*",
            module=r"sqlmesh\.core\.model\.definition",
        )


def _themed_widget(quak):
    """quak's widget, with the two rules its shipped stylesheet puts out of reach.

    The table is drawn in a shadow root without ``::part`` exports. Custom properties cross
    the boundary (``~/.jupyter/custom/custom.css`` relies on that), but the inline scroll
    height and the row hover, which shares ``--light-silver`` with every border, do not.

    So the widget's ES module is composed here: quak's bundle with its export renamed,
    followed by a wrapper adding a stylesheet to the same open shadow root.

    Args:
        quak: The imported module, whose Widget is subclassed and whose bundle is read.

    Returns:
        The Widget subclass to render a dataframe with.
    """
    import re

    # _esm is anywidget's FileContents, whose str() is the bundle itself.
    bundle = str(quak.Widget._esm)
    # A numeric column holding one value has a zero range, which Mosaic's bin function turns
    # into NaN bounds: every row lands in the null bin and the axis reads -Infinity and
    # Infinity on top of each other. Widened around the value instead, so it is one bar.
    # Matched by the only function destructuring {step:...,steps:...=25}, for the same reason
    # the export is; a release without it keeps the bug rather than failing to load.
    bundle = re.sub(
        r"(function [\w$]+\(([\w$]+),([\w$]+),[\w$]+\)\{)(let\{step:[\w$]+,steps:[\w$]+=25\})",
        r"\1\2===\3&&(\2-=1,\3+=1);\4",
        bundle,
        count=1,
    )
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

    A dataframe's plain repr truncates to a few rows. quak replaces IPython's display
    formatter, so no cell has to call anything; anything that is not a dataframe goes
    through unchanged. Skipped when quak, an operator's ``JUPYTER_EXTENSIONS`` entry, is not
    installed.
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


def _report(message: str) -> None:
    """Print a line under the first cell that runs.

    Output before the first execute request lands in the Log Console, which nobody has open.

    Args:
        message: The line to print.
    """
    ipython = get_ipython()  # noqa: F821 -- IPython provides this in the namespace.

    def once(info) -> None:
        ipython.events.unregister("pre_run_cell", once)
        print(message)

    ipython.events.register("pre_run_cell", once)


def _load() -> None:
    """Load the extensions and open the project."""
    _silence_seed_warnings()
    _explore_columns()

    from sqlmesh.magics import register_magics
    from sqlnotebook.project import find_project

    ipython = get_ipython()  # noqa: F821 -- IPython provides this in the namespace.
    # Called rather than loaded as an extension: SQLMesh registers no IPython extension
    # hook, and its own automatic registration on import only fires in an environment it
    # recognises as a notebook.
    register_magics()
    ipython.run_line_magic("load_ext", "sqlnotebook.kernel")

    project = find_project()
    if project is None:
        return

    ipython.run_line_magic("context", str(project))
    context = ipython.user_ns.get("context")
    if context is None:
        return

    # Which account and which environment, because both are answers a person otherwise has
    # to go and look up, and getting the second one wrong means planning over production.
    _report(
        f"SQLMesh project {project.name} as {os.environ.get('SQLMESH_USER', 'unknown')}, "
        f"target environment {context.config.default_target_environment}."
    )


try:
    _load()
except Exception as error:  # noqa: BLE001 -- a kernel that starts can be debugged in.
    _report(f"SQLMesh could not be loaded: {error}")
