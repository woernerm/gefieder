"""Reading a SQLMesh model as notebook cells, and writing those cells back as SQL.

The alternative designs all keep two files. A notebook beside each model means generated
SQL that must not be hand-edited, a sync step, and a reviewer reading JSON. Committing the
notebook instead means the diff a regulated audit reads is a JSON blob.

So there is one file, and the notebook is a view of it. Nothing is generated, so nothing
can be overwritten, and ``git diff`` shows SQL because SQL is what is on disk.

Cells are split on the same ``# %%`` markers jupytext uses, spelled ``-- %%`` for SQL. A
model that has never been opened here has none, and is one cell -- which is the whole file,
which is what SQLMesh parses.
"""
import nbformat

CELL_MARKER = "-- %%"
"""What separates cells inside a .sql file. A SQL comment, so the file stays valid SQL and
SQLMesh parses a marked-up model exactly as it parses a plain one."""

MARKDOWN_MARKER = f"{CELL_MARKER} [markdown]"
"""A prose cell, its lines commented out. Written above the model definition it explains,
where SQLMesh reads a leading comment as the model's description -- so documentation
written here reaches the documentation pages."""


def to_notebook(text: str) -> nbformat.NotebookNode:
    """Read the source of a model file as notebook cells.

    Args:
        text: The contents of the .sql file.

    Returns:
        A notebook whose cells are the marked regions, or a single code cell holding
        everything when the file carries no markers.
    """
    notebook = nbformat.v4.new_notebook()
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

    A single code cell -- the shape a model keeps unless someone deliberately splits it --
    is written out bare, so opening a model and saving it leaves the file byte for byte as
    it was. Anything else gets its markers.

    Args:
        notebook: The notebook being saved.

    Returns:
        The text to write to the .sql file.
    """
    cells = [cell for cell in notebook.cells if cell.source.strip()]
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


