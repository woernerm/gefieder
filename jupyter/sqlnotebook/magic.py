"""Running a MODEL cell: validate the definition and preview the rows it produces.

SQLMesh's own ``%%model`` magic writes the cell back to the model's file and reformats it.
Here the file is what the editor has open, so writing it from underneath would race the
editor and reformatting would rewrite a diff nobody asked for. This does the rest: parse the
definition, register it with the context, and show what it produces.

The preview evaluates against the database, which needs the model to be part of a loaded
project -- so it works on a model that has been saved at least once, and says so plainly
when it has not.
"""
from IPython.core.magic import Magics, cell_magic, magics_class
from IPython.display import display

PREVIEW_LIMIT = 20
"""Rows shown under a model cell. Enough to see the shape of the result; ``%evaluate`` in a
cell of its own is there when more is wanted."""


@magics_class
class ModelMagics(Magics):
    """The MODEL cell of a model notebook."""

    @staticmethod
    def _context(shell):
        """The SQLMesh context the startup file put in the namespace.

        Args:
            shell: The InteractiveShell holding the user namespace.

        Returns:
            The Context.

        Raises:
            RuntimeError: There is none, which means the kernel started outside a project.
        """
        context = shell.user_ns.get("context")
        if context is None:
            raise RuntimeError(
                "No SQLMesh project is loaded. Open this notebook from inside the models "
                "workspace, or run %context <path> first."
            )
        return context

    @cell_magic
    def model_cell(self, line, cell):
        """Validate a model definition and show what it produces.

        Args:
            line: The magic's arguments, unused: the model names itself in its own MODEL
                block, so there is nothing to pass.
            cell: The model definition.
        """
        from sqlmesh.core.dialect import parse
        from sqlmesh.core.model import load_sql_based_model
        from sqlmesh.utils.errors import SQLMeshError

        context = self._context(self.shell)
        # Picks up edits made to other files -- an upstream model, a macro -- since the
        # kernel started, so a cell is never rendered against a stale project.
        context.refresh()

        defaults = context.config.model_defaults
        # The project's model defaults, or a model would silently lose the start date and
        # cron every other model in the project has.
        model = load_sql_based_model(
            parse(cell, default_dialect=defaults.dialect),
            macros=context._macros,
            jinja_macros=context._jinja_macros,
            # The project's audits, or a model naming one in its audits() clause fails to
            # load: the name is resolved against this and none is known. Spelled
            # ``audit_definitions``, which reaches the model through **kwargs -- the
            # ``audits`` parameter beside it is accepted and then dropped.
            audit_definitions=context._audits,
            dialect=defaults.dialect,
            default_catalog=context.default_catalog,
            defaults=defaults.dict(),
        )
        context.upsert_model(model)

        print(f"{model.name} parsed.")

        try:
            display(context.evaluate(
                model.name,
                start=model.start,
                end="now",
                execution_time="now",
                limit=PREVIEW_LIMIT,
            ))
        except SQLMeshError as error:
            # A model that has never been saved has no snapshot, and one whose upstream has
            # not been built has nothing to read. Both are ordinary states mid-edit, so they
            # are reported rather than raised over a definition that parsed.
            print(f"No preview: {error}")
