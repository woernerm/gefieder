"""Wiring the model-file translation into Jupyter.

Jupyter asks a ContentsManager for a file's content and hands back what it saved, so
answering a ``.sql`` request of type ``notebook`` with cells is all it takes for a model to
open as a notebook. The translation itself is in :mod:`sqlnotebook.cells`, which imports
nothing from Jupyter and is therefore testable on its own.

Built on jupytext's manager rather than beside it. jupytext derives its own class from
whichever manager is configured and its ``save`` refuses an extension it does not know, so
a manager configured *under* it would never see a ``.sql`` save. Deriving from it instead
puts this class on the outside: ``.sql`` is handled here and everything else -- a paired
Python model, an ``.ipynb`` -- falls through to jupytext exactly as it would without us.
"""
import nbformat
from jupyter_server.services.contents.filemanager import FileContentsManager
from jupytext import build_sync_jupytext_contents_manager_class

from .cells import from_notebook, to_notebook


class ModelContentsManager(
    build_sync_jupytext_contents_manager_class(FileContentsManager)
):
    """Serves .sql files as notebooks, and saves them back as .sql.

    Everything else -- .ipynb, .py, .yaml -- is left to jupytext and the base class, so an
    analysis notebook, a Python model and an audit behave exactly as they do in any Jupyter.
    """

    @staticmethod
    def _is_model(path: str) -> bool:
        return path.endswith(".sql")

    def get(self, path, content=True, type=None, format=None):
        """Return a file, rendering a .sql one as a notebook when asked for one.

        Jupyter asks with ``type="notebook"`` because the frontend registers .sql as a
        notebook file type; the file browser and the editor ask for a file and still get
        the text, which is what keeps "Open With -> Editor" working.

        Args:
            path: The API path of the file.
            content: Whether to include the content.
            type: The type the caller wants it as.
            format: The requested format, passed through.

        Returns:
            The content model.
        """
        if type != "notebook" or not self._is_model(path):
            return super().get(path, content=content, type=type, format=format)

        model = super().get(path, content=content, type="file", format="text")
        model["type"] = "notebook"
        model["format"] = "json" if content else None
        if content:
            model["content"] = to_notebook(model["content"])
            self.validate_notebook_model(model)
        return model

    def save(self, model, path=""):
        """Save a file, writing a notebook back to a .sql file as its cells' text.

        Args:
            model: The content model being saved.
            path: The API path to save it at.

        Returns:
            The saved content model, without content, as Jupyter expects.
        """
        if model.get("type") != "notebook" or not self._is_model(path):
            return super().save(model, path)

        text = from_notebook(nbformat.from_dict(model["content"]))
        saved = super().save({"type": "file", "format": "text", "content": text}, path)
        # Answered as what was asked for, or the frontend treats the save as a type change
        # and offers to save a copy as .ipynb instead.
        saved["type"] = "notebook"
        return saved
