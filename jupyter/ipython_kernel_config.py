"""What every SQLMesh kernel runs before the first cell.

``exec_files`` rather than a file in ``profile_default/startup/``: a kernel is launched by
``ipykernel_launcher``, which does not run a profile's startup files, so one dropped there
is silently never executed.

Named ``ipython_kernel_config.py``, which is the file a kernel reads. An
``ipython_config.py`` beside it is read by the ``ipython`` command and ignored here, which
looks identical from the outside: the kernel starts, and the first cell fails on a context
that was never created.
"""
c = get_config()  # noqa: F821 -- IPython injects this into the file's namespace.

c.InteractiveShellApp.exec_files = ["/opt/notebook/etc/sqlmesh_startup.py"]
