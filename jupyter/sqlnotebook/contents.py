"""Wiring the model-file translation into Jupyter.

Answering a ``.sql`` request of type ``notebook`` with cells makes a model open as a
notebook; :mod:`sqlnotebook.cells` does the translation without Jupyter imports.

Derived from jupytext's manager, as jupytext wraps whichever manager is configured and
refuses to save unknown extensions. So ``.sql`` is handled here and everything else falls
through to jupytext.
"""
import nbformat
from jupyter_server.services.contents.filemanager import FileContentsManager
from jupytext import build_sync_jupytext_contents_manager_class

from .branch import switch_off_main
from .cells import KERNELSPEC, from_notebook, to_notebook


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

        The file browser and the editor ask for a file and still get the text, which keeps
        "Open With -> Editor" working.

        Args:
            path: The API path of the file.
            content: Whether to include the content.
            type: The type the caller wants it as.
            format: The requested format, passed through.

        Returns:
            The content model.
        """
        if type != "notebook" or not self._is_model(path):
            model = super().get(path, content=content, type=type, format=format)
            # A Python model is a notebook jupytext builds, and it records no kernel --
            # so Jupyter would ask on every open, the .py having nowhere to keep one.
            if type == "notebook" and content and path.endswith(".py"):
                model["content"].setdefault("metadata", {}).update(KERNELSPEC)
                self.notary.mark_cells(model["content"], True)
            return model

        model = super().get(path, content=content, type="file", format="text")
        model["type"] = "notebook"
        model["format"] = "json" if content else None
        if content:
            model["content"] = to_notebook(model["content"])
            # Trust guards the outputs a notebook file stores, and a model file stores
            # none, so there is nothing to distrust. Unmarked, Lab counts every cell as
            # untrusted and flags the notebook; a signature could never vouch for it, the
            # cells being built afresh, with new ids, on every open.
            self.notary.mark_cells(model["content"], True)
            self.validate_notebook_model(model)
        return model

    def save(self, model, path=""):
        """Save a file, writing a notebook back to a .sql file as its cells' text.

        Every save passes here, so this moves a workspace off the deploying branch. The new
        branch is not reported back, as Lab drops extra response fields; the git panel shows it.

        Args:
            model: The content model being saved.
            path: The API path to save it at.

        Returns:
            The saved content model, without content, as Jupyter expects.
        """
        switch_off_main(self._get_os_path(path))
        if model.get("type") != "notebook" or not self._is_model(path):
            return super().save(model, path)

        text = from_notebook(nbformat.from_dict(model["content"]))
        saved = super().save({"type": "file", "format": "text", "content": text}, path)
        # Answered as what was asked for, or the frontend treats the save as a type change
        # and offers to save a copy as .ipynb instead.
        saved["type"] = "notebook"
        return saved
