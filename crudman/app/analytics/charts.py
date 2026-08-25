"""Building one ECharts option object from a panel's query result, chart and bindings.

The result reaches the chart as a chain of ECharts datasets, each addressed by **id**
rather than by index:

    query  -- the rows as they came back, dimensions named after the columns
    shaped -- the panel's transforms: which rows, grouped how
    chart  -- the chart's own transforms: sorting, trimming

Ids rather than indices because a chart used to have to know how many datasets came
before its own and count from there; with ``fromDatasetId`` nothing counts anything, and
a stage that does not exist for a given panel simply is not in the chain. A series reads
the last stage unless it names a ``datasetId`` itself.

Nothing here decides what the chart looks like -- that is the stored option object's job.
What this module does is fill in the two things the option object deliberately does not
know: where its data comes from, and which column each ``${slot}`` meant.
"""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

from . import parameters


class ChartBuildError(Exception):
    """A chart's stored options could not be assembled, with a message fit to show."""


QUERY_ID = "query"
SHAPED_ID = "shaped"
CHART_ID = "chart"
"""The three dataset stages, in the order they chain."""


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


def _expand(series, slots, bound):
    """Repeat a series once per column for the one slot written as a list.

    A stacked bar over three measures is three series that differ only in the column
    they read, so the chart declares one carrying ``${measures[]}`` and it is repeated
    here. Each copy is named after its column, which is what the legend shows.

    Args:
        series: The series list from the chart's options, before slot resolution.
        slots: Slot name to whether it takes a list.
        bound: The panel's bindings, read only for the list slot's own columns.

    Returns:
        The series list with every list slot expanded.
    """
    expanded = []
    for entry in series:
        listed = [
            name for name, is_list in slots.items()
            if is_list and f"${{{name}[]}}" in _strings(entry)
        ]
        if not listed:
            expanded.append(entry)
            continue

        # One list slot per series: a second would make the count of series a product of
        # two bindings, which is a chart nobody asked for.
        name = listed[0]
        columns = bound.get(name) or []
        columns = columns if isinstance(columns, list) else [columns]

        # Nothing bound leaves the series as written, token and all. Dropping it would
        # render an empty chart and report nothing, where every other unbound slot is
        # left in place precisely so the mistake is visible.
        if not columns:
            expanded.append(entry)
            continue

        for column in columns:
            copy = _replace(entry, f"${{{name}[]}}", column)
            if isinstance(copy, dict):
                copy.setdefault("name", column)
            expanded.append(copy)

    return expanded


def _strings(node):
    """Every string anywhere inside a structure, so a token can be looked for."""
    if isinstance(node, dict):
        return [text for value in node.values() for text in _strings(value)]
    if isinstance(node, list):
        return [text for value in node for text in _strings(value)]
    return [node] if isinstance(node, str) else []


def _replace(node, token, value):
    """Replace a whole-string token wherever it appears in a structure."""
    if isinstance(node, dict):
        return {key: _replace(item, token, value) for key, item in node.items()}
    if isinstance(node, list):
        return [_replace(item, token, value) for item in node]
    return value if node == token else node


def build(panel, columns, rows):
    """The option object to hand to echarts.setOption.

    Args:
        panel: The Panel being drawn; its chart, transforms and bindings are read.
        columns: Column names of the query result, in order.
        rows: The query result rows.

    Returns:
        The chart's stored options with the dataset chain added, every bound slot
        replaced by its column, and any list slot expanded into one series per column.
        An unbound slot is left as written, so it fails where it can be seen rather than
        quietly plotting nothing.

    Raises:
        ChartBuildError: The stored options are not shaped like an option object. They
            are author-written JSON that no validation can fully vet, so the panel says
            so on its own card rather than taking the page down with a server error.
    """
    chart = panel.chart
    bound = panel.bindings or {}

    datasets = [{
        "id": QUERY_ID,
        "dimensions": list(columns),
        "source": [[_plain(value) for value in row] for row in rows],
    }]
    source = QUERY_ID

    # The panel's transforms name real columns: it is the one place that knows both the
    # query and what is wanted from it, so there is nothing to resolve.
    if panel.transforms:
        datasets.append({
            "id": SHAPED_ID,
            "fromDatasetId": source,
            "transform": panel.transforms,
        })
        source = SHAPED_ID

    # The chart's do not, having been written without a query in mind.
    if chart.transforms:
        datasets.append({
            "id": CHART_ID,
            "fromDatasetId": source,
            "transform": parameters.resolve(chart.transforms, bound),
        })
        source = CHART_ID

    options = deepcopy(chart.options or {})
    declared = options.pop("series", None) or []
    if not isinstance(declared, list):
        raise ChartBuildError("The chart's series must be a list.")

    # Expanded before resolution so a list slot's own token is gone by the time the
    # remaining slots are replaced, and every series is resolved the same way.
    series = _expand(declared, chart.slots, bound)
    if not all(isinstance(entry, dict) for entry in series):
        raise ChartBuildError("Every entry in the chart's series must be an object.")

    options = parameters.resolve(options, bound)
    options["dataset"] = datasets
    options["series"] = [
        {"datasetId": source, **parameters.resolve(entry, bound)} for entry in series
    ]

    return options
