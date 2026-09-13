"""What the Contextual Help panel shows for a model or macro name under the cursor.

The panel asks the kernel about the token at the cursor, and IPython answers for Python
names alone -- so in a SQL cell it stays empty. Here the token is looked up in the loaded
project instead: a model name gets the model's description and its columns, with their
types and descriptions; a macro gets its docstring; a column name gets its type and
description in every model the cell names. That is what an upstream model's file says
about itself, brought to the place where it is used.
"""
import inspect
import re

TOKEN = re.compile(r"@?[\w.]+")
"""A model reference or a macro call, dotted parts and all; the ``@`` says which."""

MODEL_NAME = re.compile(r"\b\w+\.[\w.]+")
"""What in a cell may name a model: dotted, as every reference to one is."""


def token_at(code: str, cursor_pos: int) -> str:
    """The token the cursor is on or right behind, or an empty string.

    Args:
        code: The cell's text.
        cursor_pos: The cursor's offset in it.

    Returns:
        The token, without a dot the person is still typing after.
    """
    for match in TOKEN.finditer(code):
        if match.start() <= cursor_pos <= match.end():
            return match.group().rstrip(".")
    return ""


def _model(context, name: str):
    """The model a name refers to, or None.

    A reference may carry a column -- ``schema.table.column`` in a macro argument -- so
    the name is shortened from the right until a model answers to it.

    Args:
        context: The loaded SQLMesh Context.
        name: A dotted name from the cell.

    Returns:
        The model, or None when no prefix of the name is one.
    """
    parts = name.split(".")
    while parts:
        try:
            model = context.get_model(".".join(parts))
        except Exception:  # noqa: BLE001 -- a keyword or a half-typed name is no model.
            model = None
        if model is not None:
            return model
        parts.pop()
    return None


def _column(context, code: str, column: str) -> str | None:
    """The column's type and description in every model the cell names, or None.

    The models are the dotted names in the cell -- its own in the MODEL block, the
    upstream ones after FROM and JOIN -- which is closer to what the person means than a
    search of the whole project, and works before the cell has been saved and loaded.

    Args:
        context: The loaded SQLMesh Context.
        code: The cell's text.
        column: The column name, without any alias or model in front of it.

    Returns:
        A markdown table, or None when no named model has the column.
    """
    rows = []
    for name in dict.fromkeys(match.group().rstrip(".") for match in MODEL_NAME.finditer(code)):
        model = _model(context, name)
        columns = model.columns_to_types if model is not None else None
        if columns and column in columns:
            rows.append(
                f"| {model.name} | {columns[column].sql(dialect=model.dialect)} "
                f"| {model.column_descriptions.get(column, '')} |"
            )
    if not rows:
        return None
    return "\n".join([f"**{column}**", "", "| Model | Type | Description |", "|---|---|---|", *rows])


def describe(context, code: str, cursor_pos: int) -> str | None:
    """Markdown about the model, macro or column under the cursor, or None.

    What answers to no model at all, ``effort`` or ``lhs.effort``, is a column.

    Args:
        context: The loaded SQLMesh Context.
        code: The cell's text.
        cursor_pos: The cursor's offset in it.

    Returns:
        The markdown to render, or None.
    """
    from sqlmesh.utils.lineage import generate_markdown_description

    token = token_at(code, cursor_pos)
    if not token:
        return None
    if token.startswith("@"):
        macro = context._macros.get(token[1:].lower())
        doc = inspect.getdoc(macro.func) if macro is not None else None
        return f"**{token}**\n\n{doc}" if doc else None

    model = _model(context, token)
    if model is None:
        return _column(context, code, token.rsplit(".", 1)[-1])
    try:
        body = generate_markdown_description(model)
    except Exception:  # noqa: BLE001 -- a query that fails to render still has a description.
        body = model.description
    return f"**{model.name}** ({model.kind.name.value})\n\n{body or ''}".rstrip()
