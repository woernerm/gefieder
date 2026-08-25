"""The shaping and presentation pipes, and what a query's columns become after them.

A panel's transforms shape the rows -- which of them, grouped how -- and a chart's
transforms present the result, sorting or trimming it. Both are ECharts
``dataset.transform`` entries and both run in the browser, which is what lets one query
serve several panels: the rows are fetched once and regrouped per panel.

The vocabulary is closed on purpose. It is not a restriction for its own sake: the panel
form offers a dropdown of the columns a chart may bind to, and that list is only exact
while every transform's effect on the column names can be worked out here without
running anything. ``filter`` and ``sort`` leave the columns alone; ``aggregate`` names
its own; anything else would leave the dropdown guessing.
"""

AGGREGATE = "ecSimpleTransform:aggregate"
"""Grouping. Not an ECharts built-in -- see the vendored ecSimpleTransform.js."""

ALLOWED = ("filter", "sort", AGGREGATE)
"""Every transform type a query or chart may name."""


class TransformError(Exception):
    """A transform is not one of ALLOWED, or is missing what it needs."""


def validate(transforms):
    """Check a pipe before it is stored.

    Args:
        transforms: The list of transform entries to check.

    Raises:
        TransformError: The pipe is not a list, or an entry is unusable.
    """
    if not isinstance(transforms, list):
        raise TransformError("Transforms must be a JSON list.")

    for entry in transforms:
        if not isinstance(entry, dict):
            raise TransformError("Each transform must be a JSON object.")

        kind = entry.get("type")
        if kind not in ALLOWED:
            raise TransformError(
                f"Unknown transform {kind!r}; use one of {', '.join(ALLOWED)}."
            )

        if kind == AGGREGATE:
            config = entry.get("config") or {}
            if not config.get("groupBy"):
                raise TransformError(f"{AGGREGATE} needs config.groupBy.")
            dimensions = config.get("resultDimensions")
            if not isinstance(dimensions, list) or not dimensions:
                raise TransformError(
                    f"{AGGREGATE} needs a non-empty config.resultDimensions."
                )
            for dimension in dimensions:
                if not isinstance(dimension, dict) or not dimension.get("from"):
                    raise TransformError(
                        "Each resultDimensions entry needs a 'from' naming a column."
                    )


def columns_after(transforms, columns):
    """The column names left once a pipe has run.

    Args:
        transforms: The pipe, as stored.
        columns: The column names entering it.

    Returns:
        The column names leaving it. Aggregate replaces them with the names its
        resultDimensions declare -- each entry's own ``name``, or the ``from`` it reads,
        which is what ecSimpleTransform defaults to. Filter and sort return them
        unchanged, dropping rows rather than columns.
    """
    for entry in transforms or []:
        if entry.get("type") == AGGREGATE:
            dimensions = (entry.get("config") or {}).get("resultDimensions") or []
            columns = [
                dimension.get("name") or dimension.get("from")
                for dimension in dimensions
                if isinstance(dimension, dict) and dimension.get("from")
            ]
    return list(columns)
