# panels

Chart panels whose query and configuration are rows in the database rather than code, so
a panel can be created at runtime -- shared between installations, or written by an
assistant -- without a deployment.

## How a panel reaches a page

1. A template places a card and nothing more.
2. HTMX (which Unfold already loads) fetches `panels/<slug>/data/` as soon as the card is
   on the page.
3. The view runs the panel's SQL and returns a `<div class="echarts-panel">` carrying the
   ECharts option object.
4. `panels.init.js` turns that div into a chart, in the admin's own colours, and redraws
   it when the theme changes.

One request per panel, so a page carrying several issues their queries at the same time
and a slow one delays only its own card.

## Placing a panel

Anywhere a template can load Unfold's component tag:

```django
{% load unfold %}
{% component "panels/panel.html" with component_class="PanelComponent" panel="open-issues" %}{% endcomponent %}
```

On a change form, through the ModelAdmin hooks Unfold provides:

```python
class MyAdmin(ModelAdmin):
    change_form_after_template = "myapp/panels.html"
```

On the dashboard, by ticking "show on dashboard" on the panel itself -- the dashboard is
shared by every installation, so it takes its panels from that flag rather than from a
template. See `crudman/app/templates/admin/index.html`.

## Why the queries are safe to store

The `panels` connection authenticates as the **analytics role**, the one Grafana uses.
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

Note what this does **not** provide: a panel author sees everything the analytics role
sees, every tenant included. Authoring a panel is therefore an analyst-level right, which
is why `panels` is absent from `sso.roles.MANAGED_APPS` -- the three provider ranks carry
no panel permission at all and someone has to be given it deliberately.

## Parameters

Values are bound, never interpolated:

```sql
SELECT tenant_id, open_issues FROM gold.issue_metrics WHERE tenant_id = %(tenant)s
```

with `{"tenant": "project_a"}` in the panel's *parameters*. Only the panel's own stored
defaults are bound; nothing is read from the query string, so following a link cannot
choose what a stored statement runs with.

## Chart configuration

`chart_type` picks the shape (bar, line, pie, scatter, table). The category axis is the
first result column and every other column becomes a series, unless *category column* and
*value columns* say otherwise. Anything else ECharts can do is reachable through
*ECharts options*, a JSON object merged over the generated one.
