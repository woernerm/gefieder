"""What makes a cell of SQL do the right thing without anybody typing a magic.

A model file's one cell is a MODEL definition, and prefixing it with ``%%model`` would
change the file. So an input transformer routes each cell to SQLMesh's magics:

- a MODEL definition is rendered, validated and previewed,
- a bare query is fetched into a dataframe,
- anything else is Python, untouched.

Unlike ``%%model``, nothing rewrites the file the editor holds open.
"""
import re

MODEL_START = re.compile(r"^\s*(--[^\n]*\n|\s*\n)*\s*MODEL\s*\(", re.IGNORECASE)
"""A model definition: the MODEL block, past any leading comment or blank line."""

QUERY_START = re.compile(r"^\s*(SELECT\b|WITH\s+[A-Za-z_][\w.]*\s+AS\s*\()", re.IGNORECASE)
"""A bare query, which is worth running and showing rather than executing as Python.

A CTE is matched by its ``<name> AS (`` rather than by the word alone: Python's ``with``
statement opens the same way and is far more common in a notebook than a query is rare.
"""


def route(lines: list[str]) -> list[str]:
    """Prefix a cell of SQL with the magic that runs it.

    An IPython input transformer, so it sees every cell before it is executed and leaves
    everything it does not recognise exactly as typed.

    Args:
        lines: The cell's lines, each ending in a newline.

    Returns:
        The lines to execute.
    """
    source = "".join(lines)
    if not source.strip() or source.lstrip().startswith(("%", "!", "?")):
        return lines
    if MODEL_START.match(source):
        return ["%%model_cell\n", *lines]
    if QUERY_START.match(source):
        return ["%%fetchdf\n", *lines]
    return lines


def load_ipython_extension(ipython):
    """Register the magic and the transformer in a kernel.

    Args:
        ipython: The InteractiveShell to register with.
    """
    from .magic import ModelMagics

    ipython.register_magics(ModelMagics)
    ipython.input_transformers_cleanup.append(route)
