# analytics

Charts whose SQL and configuration are rows in the database rather than code, so a metric
can be added at runtime -- shared between installations, or written by an assistant --
without a deployment.

## The four models

A metric is split so that each piece can be authored once and reused:

| model | holds | knows nothing about |
|---|---|---|
| **Query** | one SQL statement and its `${name}` placeholders | what will be drawn from it |
| **Chart** | one ECharts option object, naming `${slot}` tokens | which query fills them |
| **Panel** | which query, which chart, the parameter values, the shaping transforms, which column each slot reads, and where it sits on the grid | — |
| **Dashboard** | an ordered set of panels and the width of its grid | — |

The rule that makes it hold together: **a query and a chart never mention each other.**
Everything that joins them is a panel field. So one query can feed several panels -- the
rows are fetched once and regrouped per panel -- and one chart, once it looks right, can
be pointed at any query that has the columns.

The examples ship that way: `example-issues-by-state` is queried once and drawn twice,
rolled up as a bar chart and pivoted as a table.

## How a panel reaches a page

1. A template places a card and nothing more.
2. HTMX (which Unfold already loads) fetches `analytics/<slug>/data/` as soon as the card
   is on the page.
3. The view runs the panel's query and returns a `<div class="echarts-panel">` carrying
   the ECharts option object.
4. `analytics.init.js` turns that div into a chart, in the admin's own colours, and
   redraws it when the theme changes.

One request per panel, so a page carrying several issues their queries at the same time
and a slow one delays only its own card.

**A query defined once costs one execution.** Panels that read the same query with the
same parameter values share the rows rather than each asking the database for a copy: the
first to arrive runs it, the rest reuse the result for the few seconds a page takes to
draw (`PANEL_RESULT_TTL`, 15 by default). It is a deduplication window, not a cache kept
warm -- anything longer would start serving a dashboard that disagrees with the database.

How completely they share depends on the `analytics` cache alias in `settings.py`. The
default is in-memory and therefore per-process, so with more than one gunicorn worker some
panels still run the query themselves; pointing that alias at a shared backend makes it
exact. Nothing depends on it for correctness -- a miss simply runs the query.

## Placing a panel

On a dashboard, by pointing the panel at one and giving it a span:

```django
{% component "analytics/dashboard.html" with component_class="DashboardComponent" dashboard="home" %}{% endcomponent %}
```

The dashboard whose slug is `home` is the admin index; see
`crudman/app/templates/admin/index.html`.

Anywhere else, by slug -- which is how a panel gets onto a change form:

```django
{% component "analytics/panel.html" with component_class="PanelComponent" panel="open-issues" %}{% endcomponent %}
```

```python
class MyAdmin(ModelAdmin):
    change_form_after_template = "myapp/panels.html"
```

A panel with no dashboard is exactly that: one meant to be embedded rather than laid out.

## Grid layout

A panel says how many of the grid's columns it spans and where it comes in the order;
rows pack themselves. There is deliberately no way to name a row and a column, so a grid
whose cells do not line up **cannot be described** -- there is nothing to validate and no
layout solver to write.

Below the `lg` breakpoint every panel is full width. The spans apply only above it, where
there are columns for them to span.

## Parameters

One syntax for both the SQL and the chart, so it is learned once.

`${name}` in SQL is **bound**: it becomes psycopg's `%(name)s` and the value travels
beside the statement.

```sql
SELECT tenant_id, open_issues FROM gold.issue_metrics WHERE tenant_id = ${tenant}
```

`${name:format}` is **interpolated** -- the value becomes part of the statement text.
That is what binding cannot do:

| format | emits | for |
|---|---|---|
| `identifier`, `doublequote` | `"gold"."m"` | a table or column name |
| `sqlstring` | `'o''brien'` | a quoted literal |
| `csv` | `'a', 'b'` | the contents of an `IN` list |
| `raw` | the value as it stands | the last resort; validates nothing |

A `%` in the statement is handled for you -- a `LIKE '%'` pattern and an interpolated
value containing one are both escaped before psycopg reads the text for placeholders of
its own.

Every format except `raw` is quoted by psycopg rather than by hand. Time macros will
arrive under the same syntax.

A query needs a **default for every placeholder**, because its signature is probed by
running it alone. A panel's own values are laid over those defaults.

## Why the queries are safe to store

The `analytics` connection authenticates as the **analytics role**, the one Grafana uses.
That is deliberate twice over: the read grants on silver, gold and the per-tenant bronze
schemas already exist on it, and a query written here therefore returns exactly what the
same query returns in a Grafana dashboard.

Two guards, both the database's own:

* the role holds **no write grant** on anything it can read, and
* each statement runs in a **read-only transaction** with a statement timeout.

Neither is sufficient alone. A read-only transaction can be lifted from inside by
`SET TRANSACTION READ WRITE`, which PostgreSQL accepts; what stops the write that follows
is the missing grant. Conversely the read-only transaction is what catches a statement
the grants alone would allow. `tests/test_panels.py` asserts both, from both directions.

Note what this does **not** provide. Read-only stops writes, not reads, so an
interpolated `${name:format}` can reach any table the analytics role sees -- every tenant
included. What keeps that bounded is the rule that **parameter values are stored, never
read from the query string**: following a link cannot choose what a stored statement runs
with. Authoring a query is therefore an analyst-level right, which is why `analytics` is
absent from `sso.roles.MANAGED_APPS` -- the three provider ranks carry no permission here
at all and someone has to be given it deliberately.

## Chart configuration

A chart stores a whole ECharts option object, the kind that can be pasted straight out of
<https://echarts.apache.org/examples/>. There is no field for the chart type or the axes
because the option object already says all of that.

The one edit a pasted example needs is its data. Where it named a column or carried
inline numbers, it names a slot:

```js
// The library's line-simple example ...
series: [{data: [150, 230, 224], type: 'line'}]

// ... naming a slot instead, which a panel binds to a column.
series: [{type: 'line', encode: {x: '${day}', y: '${total}'}}]
```

`${slot[]}` is a **list slot**: the series is repeated once per column bound to it, each
copy named after its column, which is what the legend shows. That is how one stacked-bar
chart serves any number of measures.

Only a string that is *entirely* a slot is replaced, so a `formatter` mentioning `${...}`
survives as written. An unbound slot is left in place -- a list slot included, whose series
is kept rather than dropped -- so it fails where it can be seen rather than quietly
plotting nothing.

### Bindings

The panel form offers a dropdown of the query's columns and **arrives already filled in**,
proposed from the name and the kind: a slot under `encode.y` on a grid wants a number, one
under `encode.x` wants a label, and on a matrix both axes want labels. Every slot stays
editable, because a proposal is a guess. See `analytics/bindings.py`.

### Transforms

Two stages, chained as ECharts datasets addressed by **id**:

```
query  -- the rows as they came back
shaped -- the panel's transforms:  which rows, grouped how
chart  -- the chart's transforms:  sorting, trimming
```

A panel's transforms name **real columns** -- it is the one place that knows both the
query and what is wanted from it. A chart's name **slots**, having been written without a
query in mind. A stage that a panel does not need is simply absent from the chain, and a
series reads the last one unless it names a `datasetId` itself.

The vocabulary is closed to `filter`, `sort` and `ecSimpleTransform:aggregate`. Not for
its own sake: the binding dropdown is only exact while each transform's effect on the
column names can be worked out without running anything.

ECharts ships **no aggregate transform** -- core has `filter`, `sort` and `boxplot` only,
and the handbook's "Aggregate" example is mis-filed under ecStat, which has none either.
So grouping comes from `static/analytics/ecSimpleTransform.js`, vendored and carrying one
documented fix: upstream's `SUM` discarded the first row of every group.

### Tables

ECharts has no table series, so a table is its **matrix coordinate system**: the row and
column headers are collected from the query result itself and each cell shows the bound
value. It needs long format -- one row per cell -- which is what the
`example-matrix-table` chart expects.

## Testing a metric

Because a query is a row that runs on its own, it can be checked on its own:

```json
{"columns": ["tenant_id", "state", "issues"], "min_rows": 1, "not_null": ["tenant_id"]}
```

```console
./manage.py check_queries [slug ...] [--refresh-signature]
```

The command runs every query with its own defaults and fails on a result that no longer
matches -- so a gold column renamed upstream breaks a pipeline rather than silently
blanking a panel. The signature probe is separate and free: it wraps the statement in
`SELECT * FROM (...) t WHERE false`, which PostgreSQL folds to a one-time false filter, so
the columns are known without reading a row. That is what lets it run on every save.
