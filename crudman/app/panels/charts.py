"""Turning a query result into an ECharts option object.

The mapping is deliberately shallow: a category column, one series per value column, and
whatever the panel's own ``options`` say merged over the top. Anything ECharts can do
that this does not name is reachable through that merge rather than through a new field.
"""

from decimal import Decimal


class ColumnMissing(Exception):
    """A panel names a result column its query does not return."""


def _plain(value):
    """A JSON-serializable stand-in for a database value.

    Args:
        value: A value as psycopg returned it.

    Returns:
        The value itself when JSON can carry it, otherwise its string form. Decimals
        become floats: ECharts plots numbers, and JSON has no decimal type.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, str, bool, type(None))):
        return value
    return str(value)


def merge(base, override):
    """Recursively merge one option object over another.

    Args:
        base: The generated options.
        override: The panel's own options, which win.

    Returns:
        A new dict; nested dicts are merged, every other value is replaced outright.
    """
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def build(panel, columns, rows):
    """Build the ECharts option object for a panel's result.

    Args:
        panel: The Panel the result belongs to.
        columns: Column names of the result, in order.
        rows: The result rows.

    Returns:
        The option object to hand to echarts.setOption.

    Raises:
        ColumnMissing: The panel names a column its own query does not return.
    """
    category = panel.category_column(columns)
    # A column named in the panel but absent from the result is a typo in the panel, so
    # it is reported in the card the way a failed query is rather than as a server error.
    if category is not None and category not in columns:
        raise ColumnMissing(
            f"The category column {category!r} is not among the columns the query "
            f"returned ({', '.join(columns) or 'none'})."
        )

    named = panel.value_columns(columns)
    missing = [name for name in named if name not in columns]
    if missing:
        raise ColumnMissing(
            f"The value column{'s' if len(missing) > 1 else ''} "
            f"{', '.join(repr(name) for name in missing)} "
            f"{'are' if len(missing) > 1 else 'is'} not among the columns the query "
            f"returned ({', '.join(columns) or 'none'})."
        )
    values = named

    index = {name: position for position, name in enumerate(columns)}

    categories = [_plain(row[index[category]]) for row in rows] if category else []

    if panel.chart_type == panel.PIE:
        # A pie has no axes: it takes name/value pairs from the first value column.
        first = values[0] if values else None
        data = [
            {"name": _plain(row[index[category]]), "value": _plain(row[index[first]])}
            for row in rows
        ] if first and category else []
        return merge(
            {
                "tooltip": {"trigger": "item"},
                "legend": {"bottom": 0},
                "series": [{"type": "pie", "radius": ["40%", "70%"], "data": data}],
            },
            panel.options,
        )

    series = [
        {
            "name": name,
            "type": panel.chart_type,
            "data": [_plain(row[index[name]]) for row in rows],
        }
        for name in values
    ]

    return merge(
        {
            "tooltip": {"trigger": "axis"},
            "legend": {"bottom": 0, "show": len(series) > 1},
            "grid": {"left": 8, "right": 8, "bottom": 32, "top": 16, "containLabel": True},
            "xAxis": {"type": "category", "data": categories},
            "yAxis": {"type": "value"},
            "series": series,
        },
        panel.options,
    )
