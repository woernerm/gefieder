"""What must hold for a model file to be safe to open as a notebook.

The one property everything rests on: opening a model and saving it changes nothing. If
that fails, every model in the repository gets rewritten the first time somebody clicks it,
and the diff a reviewer reads is noise. So the round trip is tested against the models this
release actually ships, not only against examples written for the test.
"""
import json
import os
from pathlib import Path

import nbformat
import pytest
from sqlnotebook.cells import from_notebook, to_notebook
from sqlnotebook.kernel import route

MODELS_DIR = Path(os.environ.get("TEST_MODELS_DIR", Path(__file__).resolve().parents[2] / "sqlmesh" / "models"))
"""The models to round-trip. From the environment because these tests run inside the
jupyter image, where the repository is mounted rather than checked out."""

MODELS = sorted(MODELS_DIR.rglob("*.sql"))
"""Every SQL model in the seed project."""


def test_there_are_models_to_check():
    """A wrong path would otherwise make every parameterized test below vanish silently."""
    assert MODELS, f"no .sql models found under {MODELS_DIR}"


def cells(*sources):
    """A notebook of the given cells; a tuple is (type, source)."""
    notebook = nbformat.v4.new_notebook()
    for source in sources:
        kind, text = source if isinstance(source, tuple) else ("code", source)
        notebook.cells.append(
            nbformat.v4.new_markdown_cell(text)
            if kind == "markdown"
            else nbformat.v4.new_code_cell(text)
        )
    return notebook


@pytest.mark.parametrize("path", MODELS, ids=lambda p: p.name)
def test_a_shipped_model_survives_being_opened_and_saved(path):
    """Byte for byte: a model nobody edited must not appear in a diff."""
    original = path.read_text()
    assert from_notebook(to_notebook(original)) == original


@pytest.mark.parametrize("path", MODELS, ids=lambda p: p.name)
def test_a_shipped_model_opens_as_one_cell(path):
    """A model is one definition, so it is one cell until somebody splits it. Anything
    else would mean the markers are being found where they were not written."""
    assert len(to_notebook(path.read_text()).cells) == 1


def test_an_empty_file_opens_as_an_empty_cell():
    """A new model is created empty; a notebook with no cells cannot be typed into."""
    notebook = to_notebook("")
    assert len(notebook.cells) == 1
    assert notebook.cells[0].source == ""


def test_splitting_into_cells_round_trips():
    notebook = cells(
        ("markdown", "Issues, harmonized.\n\nOne row per change."),
        "MODEL (\n  name silver.issues\n);\n\nSELECT 1 AS id",
        "%evaluate silver.issues",
    )
    text = from_notebook(notebook)
    reopened = to_notebook(text)

    assert [c.cell_type for c in reopened.cells] == ["markdown", "code", "code"]
    assert [c.source for c in reopened.cells] == [c.source for c in notebook.cells]
    assert from_notebook(reopened) == text


def test_a_split_file_is_still_valid_sql():
    """The markers are SQL comments, which is what lets the engine parse a file somebody
    has been editing in a notebook."""
    text = from_notebook(cells(("markdown", "Why this exists."), "SELECT 1"))
    for line in text.splitlines():
        assert line.startswith("--") or not line.strip() or "SELECT" in line


def test_prose_lands_where_sqlmesh_reads_a_description():
    """A markdown cell above the definition becomes the leading comment, which SQLMesh
    registers as the model's description -- so documentation written in the notebook
    reaches the documentation pages."""
    text = from_notebook(cells(("markdown", "Revenue per customer."), "MODEL (\n  name a.b\n);"))
    assert text.splitlines()[1] == "-- Revenue per customer."


def test_empty_cells_are_dropped():
    """Jupyter leaves a trailing empty cell behind constantly; each one would otherwise
    grow the file by a marker per save."""
    assert from_notebook(cells("SELECT 1", "", "   ")) == "SELECT 1\n"


class TestCellRouting:
    """Which magic a cell is executed with, decided from its first words."""

    @staticmethod
    def magic(source):
        routed = route(source.splitlines(keepends=True))
        return routed[0].strip() if routed and routed[0].startswith("%%") else None

    @pytest.mark.parametrize("source", [
        "MODEL (\n  name a.b\n);\nSELECT 1",
        "-- A leading comment.\nMODEL (\n  name a.b\n);\nSELECT 1",
        "\n\nmodel (\n  name a.b\n);\nSELECT 1",
    ])
    def test_a_definition_runs_as_a_model(self, source):
        assert self.magic(source) == "%%model_cell"

    @pytest.mark.parametrize("source", ["SELECT 1", "select * from t", "WITH x AS (SELECT 1)\nSELECT * FROM x"])
    def test_a_query_runs_as_a_query(self, source):
        assert self.magic(source) == "%%fetchdf"

    @pytest.mark.parametrize("source", [
        "df.plot()",
        "import polars as pl",
        "%evaluate silver.issues",
        "!ls",
        "",
        # The words that start a model or a query, as ordinary Python identifiers.
        "model = 3",
        "selected = [1, 2]",
        "with open('f') as handle:\n    pass",
    ])
    def test_everything_else_is_left_as_python(self, source):
        assert self.magic(source) is None

    def test_the_cell_itself_is_never_altered(self):
        """Only a prefix is added: the cell is the model file, so a transformer that
        rewrote it would rewrite what gets committed."""
        source = "MODEL (\n  name a.b\n);\nSELECT 1\n"
        assert "".join(route(source.splitlines(keepends=True))[1:]) == source


class TestDefaultViewer:
    """That a model opens as a notebook rather than in the text editor.

    Two viewers claim .sql -- jupytext's and the editor -- and the settings override is
    what decides which a double-click gets. It is a file in a directory Lab searches, so
    nothing fails loudly when it is written to the wrong one: the model simply opens as
    text, which is the whole feature gone. Hence a test on the path rather than on the
    JSON alone.
    """

    @staticmethod
    def overrides():
        """The overrides Lab actually loads, from the application directory it reports."""
        from jupyterlab.commands import get_app_dir

        return Path(get_app_dir()) / "settings" / "overrides.json"

    def test_the_overrides_are_in_the_directory_lab_reads(self):
        """A copy anywhere else -- /usr/local/share/jupyter/lab, say -- is ignored."""
        assert self.overrides().is_file(), (
            f"no overrides.json in Lab's application directory ({self.overrides().parent}); "
            "a model will open in the text editor"
        )

    def test_a_model_opens_as_a_jupytext_notebook(self):
        """The viewer name is jupytext's own; a typo in it fails the same way as a
        missing file, silently."""
        viewers = json.loads(self.overrides().read_text())[
            "@jupyterlab/docmanager-extension:plugin"
        ]["defaultViewers"]
        assert viewers["sql"] == "Jupytext Notebook"


class TestKernel:
    """That a model opens on the kernel that can run it.

    A .sql file has nowhere to record a kernel, so the notebook has to carry one or
    Jupyter asks on every open -- and offers python3, which starts and then fails every
    cell, since the magics and the project are loaded only for the sqlmesh kernel.
    """

    def test_a_model_opens_on_the_sqlmesh_kernel(self):
        spec = to_notebook("SELECT 1").metadata.kernelspec
        assert spec.name == "sqlmesh"
        assert spec.language == "sql"

    def test_the_kernel_exists_under_that_name(self):
        """The name is what Jupyter looks up; a typo asks again, silently."""
        from jupyter_client.kernelspec import KernelSpecManager

        assert "sqlmesh" in KernelSpecManager().find_kernel_specs()

    @pytest.mark.parametrize("path", MODELS, ids=lambda p: p.name)
    def test_the_kernel_never_reaches_the_file(self, path):
        """Metadata is a property of the view, not of the model: a kernel written into
        the .sql would put JSON in the file and a diff on every model."""
        original = path.read_text()
        assert "kernelspec" not in from_notebook(to_notebook(original))
