"""Reading a SQLMesh model as notebook cells, and writing those cells back as SQL.

One file, with the notebook as a view of it: nothing is generated or synced, and
``git diff`` shows SQL, not JSON. Cells are split on jupytext's ``# %%`` markers, spelled
``-- %%`` for SQL. A model without markers is one cell.
"""
import nbformat

CELL_MARKER = "-- %%"
"""What separates cells inside a .sql file. A SQL comment, so the file stays valid SQL and
SQLMesh parses a marked-up model exactly as it parses a plain one."""

MARKDOWN_MARKER = f"{CELL_MARKER} [markdown]"
"""A prose cell, its lines commented out. Written above the model definition it explains,
where SQLMesh reads a leading comment as the model's description -- so documentation
written here reaches the documentation pages."""


KERNELSPEC = {
    "kernelspec": {"name": "sqlmesh", "display_name": "SQLMesh", "language": "sql"},
}
"""The kernel a model opens on. A .sql file has nowhere to record one, so without this
Jupyter asks on every open and offers python3 -- which starts, and then fails every cell,
the magics and the project being loaded only for the kernel named here."""

SQL_LANGUAGE = {
    "language_info": {"name": "sql", "mimetype": "text/x-sql", "file_extension": ".sql"},
}
"""What the editor highlights a model's cells as.

The kernel is ipykernel and says so -- ``text/x-python`` -- and without this the editor
believes it: ``--`` opens no comment in Python and ``'`` opens a string, so an apostrophe
in a comment (``Project B's``) colours the text to the next one and the SQL below it is
highlighted as Python. Only the .sql view says this; a Python model is Python."""


def to_notebook(text: str) -> nbformat.NotebookNode:
    """Read the source of a model file as notebook cells.

    Args:
        text: The contents of the .sql file.

    Returns:
        A notebook whose cells are the marked regions, or a single code cell holding
        everything when the file carries no markers.
    """
    notebook = nbformat.v4.new_notebook(metadata={**KERNELSPEC, **SQL_LANGUAGE})
    kind, lines = "code", []

    def flush():
        # An empty region is dropped: a trailing marker, or the text before the first one,
        # would otherwise each add a cell nobody wrote.
        source = "\n".join(lines).strip("\n")
        if not source:
            return
        if kind == "markdown":
            uncommented = "\n".join(
                line[3:] if line.startswith("-- ") else line.removeprefix("--")
                for line in source.splitlines()
            )
            notebook.cells.append(nbformat.v4.new_markdown_cell(uncommented))
        else:
            notebook.cells.append(nbformat.v4.new_code_cell(source))

    for line in text.splitlines():
        if line.startswith(CELL_MARKER):
            flush()
            kind, lines = (
                "markdown" if line.startswith(MARKDOWN_MARKER) else "code",
                [],
            )
            continue
        lines.append(line)
    flush()

    if not notebook.cells:
        notebook.cells.append(nbformat.v4.new_code_cell(""))
    return notebook


def from_notebook(notebook: nbformat.NotebookNode) -> str:
    """Write notebook cells back as the source of a model file.

    A single code cell is written out bare, so opening and saving a model leaves the file
    unchanged.

    Args:
        notebook: The notebook being saved.

    Returns:
        The text to write to the .sql file.
    """
    cells = [cell for cell in notebook.cells if cell.source.strip()]
    if not cells:
        # Zero bytes, not a newline: SQLMesh skips an empty model file and parses anything
        # longer, and a lone newline is "no MODEL block" for every kernel in the workspace.
        return ""
    if len(cells) == 1 and cells[0].cell_type == "code":
        return cells[0].source.rstrip("\n") + "\n"

    parts = []
    for cell in cells:
        if cell.cell_type == "markdown":
            body = "\n".join(f"-- {line}".rstrip() for line in cell.source.splitlines())
            parts.append(f"{MARKDOWN_MARKER}\n{body}")
        else:
            parts.append(f"{CELL_MARKER}\n{cell.source.rstrip()}")
    return "\n\n".join(parts) + "\n"


