"""The configuration of each person's own JupyterLab server.

One thing it settles: that a .sql model opens as a notebook. The rest of JupyterLab is
left at its defaults, which is the point -- a person who knows Jupyter knows this.
"""
c = get_config()  # noqa: F821 -- Jupyter injects this into the file's namespace.

# What makes a model file a notebook. The frontend asks for a .sql file as a notebook
# because the settings overrides register it as one; this is what answers with cells.
c.ServerApp.contents_manager_class = "sqlnotebook.contents.ModelContentsManager"

# SQLMesh is the only kernel: one started on the stock python3 loads no magics and opens no
# project, so every model cell fails on a context that was never created. Deleting the
# kernelspec is not enough -- ipykernel carries a fallback inside the package and Jupyter
# synthesizes it back unless ensure_native_kernel says otherwise.
c.KernelSpecManager.ensure_native_kernel = False
c.KernelSpecManager.allowed_kernelspecs = ["sqlmesh"]

# Serves ~/.jupyter/custom/custom.css, off by default. The image ships one file there,
# which repoints quak's CSS variables at the theme's so its table reads as part of Lab
# rather than a light panel inside a dark one.
c.LabApp.custom_css = True

# The server is seen inside the shell's frame (crudman/app/shell/). Under the hub it
# refuses every frame unless told otherwise here, the hub's mixin keeping whatever policy
# this file sets. Its own origin only, jupyter_server's own default.
c.ServerApp.tornado_settings = {
    "headers": {"Content-Security-Policy": "frame-ancestors 'self'"},
}
