"""Thin lines over time with a faint fill beneath, no dots: the server's own charts."""
from dashboards import Chart

chart = Chart({
    "xAxis": {"type": "time"},
    "yAxis": {},
    "series": {"type": "line", "showSymbol": False, "lineStyle": {"width": 1}, "areaStyle": {"opacity": 0.1}},
})

# %% Run this cell to see the chart with sample data.
chart
