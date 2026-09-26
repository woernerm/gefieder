# dashboards

The dashboards, as code. Four kinds of file, each defined once and used by name:

| File | What it is | Named after |
|---|---|---|
| `filters.py` | the controls a viewer narrows the data with | the variable |
| `queries/<name>.sql` | SQL; `:name` is the value of a filter | the file |
| `charts/<name>.py` | how data looks: an ECharts option, without data | the file |
| `boards/<name>.py` | a dashboard: which query is shown in which chart | the file |

## The one rule: the shape of the data

A query's **first column is the x axis, every other column is a series**. So any query can
be shown in any chart: switching `"bar"` for `"line"` or `"table"` needs no other change.

A query may instead return three columns with one named `series`, one row per x and series
(long form); it is turned into the shape above before a chart sees it.

## Filters

A query reads a filter as `:name`, and what it gets depends on the kind of filter:

- `Select`: a list, so write `column = ANY(:name)`. Nothing picked means everything.
- `Since`: the moment the chosen period starts, so write `time >= :name`.

A dashboard shows the filters its queries read, and the address bar carries what is picked:
a link opens the same view. `Panel(..., click="name")` sets a filter to the bar or slice
clicked, and every panel reading it follows.

## Charts

Copy an option from the [ECharts examples](https://echarts.apache.org/examples) and leave the
data out. Write `series` as one dict to have it repeated for every series the data has, or
as a list to place each one yourself. A chart that reshapes data first -- a pareto sorts --
takes a `prepare` function. `Table()` and `Stat()` are charts too.

## Dashboards

`Panel(title, query, chart)`, with any of:

- `transform=`: a function reshaping this panel's data (a Polars DataFrame), so one query
  feeds several panels,
- `click=`: the filter a click sets,
- `unit=`: `bytes`, `bytes/s`, `percent`, or any text to put after the number,
- `wide=True`: take a whole row.

`Text(title, words)` and `Video(title, url)` explain what the panels around them show.
Panels flow in the order given and rearrange themselves to fit the window.

## Seeing it

Open any chart, board or query here and run its last cell: a chart draws itself with sample
data, a board with the real data, a query shows its rows. It is the same drawing the
dashboard does. `pytest dashboards` runs the checks in `tests/`.

A dashboard goes live the way a model does: commit, push, and the next deployment shows it.
