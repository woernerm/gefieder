"""The kernel a model notebook runs on: ipykernel, reporting SQL.

JupyterLab derives a cell's editor language from the notebook's ``language_info``, and
overwrites that metadata with whatever the kernel reports as soon as the kernel connects
(``_updateLanguage`` in its notebook model). So a model opens correctly highlighted, and a
second later turns into Python: ``--`` no longer starts a comment and ``'`` opens a string,
which colours prose and highlights words inside it.

Setting it here rather than in the notebook is what makes it stick -- the metadata written
when the file is opened is the value being overwritten.

The kernel itself is unchanged: cells are Python, the routing in :mod:`sqlnotebook.kernel`
turns a MODEL or a query into the magic that runs it, and ``language_info`` says only what
an editor should highlight.
"""
from ipykernel.ipkernel import IPythonKernel
from ipykernel.kernelapp import IPKernelApp


class SQLKernel(IPythonKernel):
    """An IPython kernel that presents itself as SQL."""

    language_info = {
        **IPythonKernel.language_info,
        "name": "sql",
        "mimetype": "text/x-sql",
        "file_extension": ".sql",
        # CodeMirror 6 takes the language from the mimetype; the mode is what Lab 3 and
        # nbconvert read, and disagreeing values there would highlight prose as Python.
        "codemirror_mode": "sql",
    }


def main() -> None:
    """Launch the kernel, the way ``ipykernel_launcher`` does."""
    IPKernelApp.launch_instance(kernel_class=SQLKernel)


if __name__ == "__main__":
    main()
