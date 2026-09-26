"""Pareto: the largest contributors first, and the share of the whole they add up to."""
import polars as pl

from dashboards import Chart


def prepare(frame: pl.DataFrame) -> pl.DataFrame:
    """Sorted from largest to smallest, with the running share added as "Share"."""
    x, value = frame.columns[:2]
    frame = frame.select(x, value).sort(value, descending=True)
    return frame.with_columns((pl.col(value).cum_sum() / pl.col(value).sum() * 100).round(1).alias("Share"))


chart = Chart(
    {
        "xAxis": {"type": "category"},
        "yAxis": [{}, {"max": 100, "axisLabel": {"formatter": "{value} %"}}],
        # A list rather than one dict: two different series, each naming its column. A
        # column is named by position or by name.
        "series": [
            {"type": "bar", "barMaxWidth": 48, "encode": {"x": 0, "y": 1}},
            {"type": "line", "name": "Share", "yAxisIndex": 1, "encode": {"x": 0, "y": "Share"}},
        ],
    },
    prepare=prepare,
)

# %% Run this cell to see the chart with sample data.
chart
