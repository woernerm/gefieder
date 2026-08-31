"""Export the SQLMesh project's documentation as JSON, for crudman to render.

Runs at image build time: loading a Context parses the project files and needs no
connection. What ships is plain text only, the structure rather than its presentation, so
the same file can feed the docs pages and later an MCP server.

Descriptions come from the models themselves: SQLMesh reads the comment block above a
MODEL() statement as its description and the inline comments on the projections as the
column descriptions.
"""

import json
import sys
from pathlib import Path

from sqlglot import exp
from sqlmesh.core.context import Context

LAYERS = ("bronze", "silver", "gold")
"""The medallion layers, in the order the docs present them."""


def _model_name(reference: str) -> str:
    """A dependency written the way a model names itself.

    SQLMesh states a dependency fully qualified and quoted (``"postgres"."silver"."x"``)
    while a model's own name carries neither catalog nor quotes.

    Args:
        reference: The dependency as SQLMesh states it.

    Returns:
        Schema and table joined by a dot, the catalog dropped.
    """
    table = exp.to_table(reference)
    return f"{table.db}.{table.name}" if table.db else table.name


def _layer_and_tenant(model) -> tuple[str | None, str | None]:
    """Where a model sits in the medallion architecture, read from its path.

    Args:
        model: The loaded SQLMesh model.

    Returns:
        The layer name and the tenant folder below it, either None when the model does
        not live under models/<layer>/ -- an external model, say.
    """
    path = getattr(model, "_path", None)
    if path is None:  # A model defined outside a file, e.g. one loaded from state.
        return None, None

    parts = Path(path).parts
    for index, part in enumerate(parts):
        if part in LAYERS:
            rest = parts[index + 1 :]
            # A file directly in the layer folder belongs to no single tenant.
            return part, rest[0] if len(rest) > 1 else None
    return None, None


def model_entry(model, dialect: str) -> dict:
    """One model's documentation, as plain text.

    Args:
        model: The loaded SQLMesh model.
        dialect: The SQL dialect the definition is rendered in.

    Returns:
        The fields the docs pages show. Rendering the definition rather than reading the
        file gives the statement SQLMesh runs, formatted the same way for every model.

        ``include_python`` drops the source of the macros a model merely calls, which
        would bury the model itself; a Python model overrides the flag and keeps its own
        function. ``render_query`` then expands the macro calls, so the SQL shown is what
        SQLMesh would run rather than the ``@macro()`` standing in for it.
    """
    layer, tenant = _layer_and_tenant(model)
    columns = model.columns_to_types or {}
    descriptions = model.column_descriptions or {}

    return {
        "name": model.name,
        "layer": layer,
        "tenant": tenant,
        "description": model.description or "",
        "kind": model.kind.name,
        "cron": model.cron,
        "owner": model.owner,
        "stamp": model.stamp,
        "tags": list(model.tags or []),
        "grains": [grain.sql(dialect=dialect) for grain in (model.grains or [])],
        "references": [ref.sql(dialect=dialect) for ref in (model.references or [])],
        "audits": [name for name, _ in (model.audits or [])],
        "depends_on": sorted(_model_name(dep) for dep in (model.depends_on or [])),
        "columns": [
            {
                "name": name,
                "type": str(data_type),
                "description": descriptions.get(name, ""),
            }
            for name, data_type in columns.items()
        ],
        "sql": "\n\n".join(
            expression.sql(dialect=dialect, pretty=True)
            for expression in model.render_definition(
                include_python=False, render_query=True
            )
        ),
    }


def export(project: Path) -> dict:
    """The whole project's documentation.

    Args:
        project: The SQLMesh project directory.

    Returns:
        The models that belong to a medallion layer, grouped by layer. Anything outside
        the three is left out, an external model documenting a source this project does
        not own.
    """
    context = Context(paths=project)
    dialect = context.config.model_defaults.dialect or "postgres"

    entries = [model_entry(model, dialect) for model in context.models.values()]
    return {
        "layers": [
            {
                "name": layer,
                "models": sorted(
                    (entry for entry in entries if entry["layer"] == layer),
                    key=lambda entry: entry["name"],
                ),
            }
            for layer in LAYERS
        ]
    }


if __name__ == "__main__":
    project = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    destination = Path(sys.argv[2] if len(sys.argv) > 2 else "docs.json")
    destination.write_text(json.dumps(export(project), indent=2) + "\n")
    print(f"Wrote {destination}")
