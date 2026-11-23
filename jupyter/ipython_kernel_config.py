"""What every SQLMesh kernel runs before the first cell.

``exec_files``, as ``ipykernel_launcher`` does not run a profile's startup files. The file
name matters: an ``ipython_config.py`` is read by the ``ipython`` command only, and the
first cell would fail on a context that was never created.
"""
c = get_config()  # noqa: F821 -- IPython injects this into the file's namespace.

c.InteractiveShellApp.exec_files = ["/opt/notebook/etc/sqlmesh_startup.py"]
