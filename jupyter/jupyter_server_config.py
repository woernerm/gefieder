"""The configuration of each person's own JupyterLab server.

One thing it settles: that a .sql model opens as a notebook. The rest of JupyterLab is
left at its defaults, which is the point -- a person who knows Jupyter knows this.
"""
c = get_config()  # noqa: F821 -- Jupyter injects this into the file's namespace.

# What makes a model file a notebook. The frontend asks for a .sql file as a notebook
# because the settings overrides register it as one; this is what answers with cells.
c.ServerApp.contents_manager_class = "sqlnotebook.contents.ModelContentsManager"
