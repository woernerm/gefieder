"""A ring: each category's share of the first column of values."""
from dashboards import Chart

chart = Chart({
    "series": {"type": "pie", "radius": ["45%", "70%"], "label": {"formatter": "{b}\n{d} %"}},
})

# %% Run this cell to see the chart with sample data.
chart
