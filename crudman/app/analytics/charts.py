"""Handing a query result to ECharts as a dataset.

A panel stores a whole ECharts option object, the kind that can be pasted straight out of
the example library at https://echarts.apache.org/examples/. What this module does is
replace the ``data`` those examples carry inline with the rows the panel's query
returned, as an ECharts ``dataset``: the column names become its dimensions, so a series
refers to a column by name rather than by position.

    option = {
      xAxis: {type: 'category'},
      yAxis: {type: 'value'},
      series: [{type: 'line', encode: {x: 'tenant_id', y: 'open_issues'}}]
    }

Nothing here decides what the chart looks like -- that is the stored option object's job,
including grouping and filtering, which ECharts does declaratively through
``dataset.transform``. Keeping the mapping in ECharts rather than in Python is what lets
an example be pasted in and work.
"""

from datetime import date, datetime
from decimal import Decimal

DATASET_KEY = "dataset"
"""Where the query result is injected, and the name an option object refers to."""

TABLE_KEY = "table"
"""Opt-in for the plain table, the one shape ECharts has no chart for.

Not an ECharts option: a panel sets ``{"table": true}`` and the rows are rendered as a
table instead of a chart. ECharts ignores keys it does not know, so it costs nothing to
carry it in the same object.
"""


def _plain(value):
    """A JSON-serializable stand-in for a database value.

    Args:
        value: A value as psycopg returned it.

    Returns:
        The value itself when JSON can carry it. Decimals become floats because ECharts
        plots numbers and JSON has no decimal type; dates become ISO strings, which its
        time axis parses. Anything else is stringified rather than risking the render.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool, type(None))):
        return value
    return str(value)


def build(panel, columns, rows):
    """The panel's stored options with the query result injected as the dataset.

    A panel that declares a dataset of its own keeps it, so an example pasted in
    unchanged -- inline data and all -- still renders; the query result is then added
    alongside it as a further dataset the option object can refer to by index.

    Args:
        panel: The Panel the result belongs to.
        columns: Column names of the result, in order.
        rows: The result rows.

    Returns:
        The option object to hand to echarts.setOption.
    """
    result = {
        "dimensions": list(columns),
        "source": [[_plain(value) for value in row] for row in rows],
    }

    options = dict(panel.options or {})
    declared = options.get(DATASET_KEY)

    if declared is None:
        options[DATASET_KEY] = result
    elif isinstance(declared, list):
        # The query result goes first because a transform that names no source defaults
        # to fromDatasetIndex 0: a transform pasted in as-is then consumes the query
        # result, which is the whole point. The cost is that a panel spelling out
        # fromDatasetIndex has to count from one, which the field's help text says.
        options[DATASET_KEY] = [result, *declared]
    else:
        options[DATASET_KEY] = [result, declared]

    return options
