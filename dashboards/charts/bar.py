"""Bars side by side: one group per category, one bar per series."""
from dashboards import Chart

chart = Chart({
    "xAxis": {"type": "category"},
    "yAxis": {},
    # One dict rather than a list: the series is repeated for every column of values.
    "series": {"type": "bar", "barMaxWidth": 48},
})

# %% Run this cell to see the chart with sample data.
chart
