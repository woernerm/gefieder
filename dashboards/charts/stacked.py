"""Bars stacked into one per category, so the height is the total."""
from dashboards import Chart

chart = Chart({
    "series": {"type": "bar", "stack": "total", "barMaxWidth": 48},
})

# %% Run this cell to see the chart with sample data.
chart
