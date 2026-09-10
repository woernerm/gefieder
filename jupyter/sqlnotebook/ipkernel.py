"""The kernel a model notebook runs on: ipykernel, reporting the language of its file.

JupyterLab derives a cell's editor language from the notebook's ``language_info``, and
overwrites that metadata with whatever the kernel reports as soon as the kernel connects
(``_updateLanguage`` in its notebook model). So a model opens correctly highlighted and a
second later turns into whatever the kernel said: setting it on the notebook is not enough,
because the notebook's value is the one being replaced.

Which language depends on the file, and one kernel serves whatever the person opens: a SQL
model is SQL, a Python model is Python. The server puts the notebook's path in
``JPY_SESSION_NAME``, which is the only thing here that knows which was opened.

The kernel itself is unchanged: cells are Python, the routing in :mod:`sqlnotebook.kernel`
turns a MODEL or a query into the magic that runs it, and ``language_info`` says only what
an editor should highlight.
"""
import os

from ipykernel.ipkernel import IPythonKernel
from ipykernel.kernelapp import IPKernelApp

SQL_LANGUAGE_INFO = {
    **IPythonKernel.language_info,
    "name": "sql",
    "mimetype": "text/x-sql",
    "file_extension": ".sql",
    # CodeMirror 6 takes the language from the mimetype; the mode is what Lab 3 and
    # nbconvert read, and disagreeing values there would highlight prose as Python.
    "codemirror_mode": "sql",
}
"""What a .sql model's cells are highlighted as."""


class SQLKernel(IPythonKernel):
    """An IPython kernel that presents itself as the language of the file it opened."""

    @property
    def language_info(self):
        """SQL for a model file, Python for everything else.

        Returns:
            The language_info a kernel_info_reply carries.
        """
        session = os.environ.get("JPY_SESSION_NAME") or ""
        return SQL_LANGUAGE_INFO if session.endswith(".sql") else IPythonKernel.language_info


def main() -> None:
    """Launch the kernel, the way ``ipykernel_launcher`` does."""
    IPKernelApp.launch_instance(kernel_class=SQLKernel)


if __name__ == "__main__":
    main()
