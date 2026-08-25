"""Example queries, charts, panels and a dashboard, created once so a fresh system has
something to look at.

Between them they show what the split is for. ``example-issues-by-state`` is queried
once and drawn twice: as a grouped bar, where a panel's shaping transform rolls the rows
up per tenant, and as a table, where the same rows are pivoted untouched. Neither chart
mentions a column -- both name ``${placeholder}`` tokens that the panels bind -- so either can
be pointed at another query without being edited.

They query the gold and silver models the SQLMesh project ships, which the example
tenants fill.

These are ordinary rows: edit one in the admin and the edit stands, deployments included.
Deleting one is not permanent, though -- the next migrate finds it missing and puts it
back. To be rid of an example for good, take its panel off the dashboard or empty its
query; both are edits and both survive.
"""

HOME_DASHBOARD = {
    "name": "home",
    "title": "Overview",
    "description": "The dashboard the admin index carries.",
    "columns": 12,
}

EXAMPLE_QUERIES = (
    {
        "ref": "example-issues-by-state",
        "title": "Issues by tenant and state",
        "description": (
            "Long format -- one row per tenant and state -- which is what lets the same "
            "result be rolled up into a bar chart and pivoted into a table. The tenant "
            "list is interpolated rather than bound, because a value cannot stand in for "
            "the contents of an IN list."
        ),
        "sql": (
            "SELECT tenant_id, state, count(*)::int AS issues\n"
            "FROM silver.issues\n"
            "WHERE tenant_id IN (${tenants:csv})\n"
            "GROUP BY tenant_id, state\n"
            "ORDER BY tenant_id, state"
        ),
        "parameter_defaults": {"tenants": ["project_a", "project_b", "project_c"]},
        "checks": {"columns": ["tenant_id", "state", "issues"], "min_rows": 1},
    },
    {
        "ref": "example-issue-metrics",
        "title": "Issue metrics per tenant",
        "description": "The precomputed gold metrics, one row per tenant.",
        "sql": (
            "SELECT tenant_id, open_issues, closed_issues, total_effort\n"
            "FROM gold.issue_metrics\n"
            "ORDER BY tenant_id"
        ),
        "checks": {
            "columns": ["tenant_id", "open_issues", "closed_issues", "total_effort"],
            "not_null": ["tenant_id"],
        },
    },
)

EXAMPLE_CHARTS = (
    {
        "ref": "example-grouped-bar",
        "title": "Grouped bar",
        "description": (
            "A bar chart the way the ECharts library writes one, with encode naming "
            "placeholders where the example had a list of numbers. ${measures[]} is a list "
            "placeholder: the series is repeated once per column bound to it, each named after "
            "its column, which is what the legend shows."
        ),
        "options": {
            "tooltip": {"trigger": "axis"},
            "legend": {"bottom": 0},
            "grid": {"left": 0, "right": 16, "top": 16, "bottom": 32, "containLabel": True},
            "xAxis": {"type": "category"},
            "yAxis": {"type": "value"},
            "series": [
                {
                    "type": "bar",
                    "encode": {"x": "${category}", "y": "${measures[]}"},
                },
            ],
        },
    },
    {
        "ref": "example-share-pie",
        "title": "Share pie",
        "description": (
            "A pie, and a presentation transform doing the sorting. The transform names "
            "a placeholder rather than a column, which is what keeps the chart reusable."
        ),
        "options": {
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0},
            "series": [
                {
                    "type": "pie",
                    "radius": ["45%", "70%"],
                    "center": ["50%", "45%"],
                    "encode": {"itemName": "${label}", "value": "${value}"},
                },
            ],
        },
        "transforms": [
            {"type": "sort", "config": {"dimension": "${value}", "order": "desc"}},
        ],
    },
    {
        "ref": "example-matrix-table",
        "title": "Table",
        "description": (
            "ECharts has no table series, so this is its matrix coordinate system: the "
            "row and column headers are collected from the query result itself, and each "
            "cell shows the value bound to ${value}. Needs long format -- one row per "
            "cell. The symbol is sized away because only the label is wanted."
        ),
        "options": {
            "tooltip": {},
            "matrix": {
                "x": {"label": {"show": True}},
                "y": {"label": {"show": True}},
                "left": 8,
                "right": 8,
                "top": 8,
                "bottom": 8,
            },
            "series": [
                {
                    "type": "scatter",
                    "coordinateSystem": "matrix",
                    "symbolSize": 0,
                    "encode": {
                        "x": "${column}",
                        "y": "${row}",
                        "value": "${value}",
                    },
                    "label": {"show": True, "position": "inside"},
                },
            ],
        },
    },
)

EXAMPLE_PANELS = (
    {
        "ref": "example-issues-per-tenant",
        "title": "Issues per tenant",
        "description": (
            "The same query as the table beside it, rolled up: the shaping transform "
            "groups the rows per tenant and sums them, so the bar chart never sees the "
            "state column."
        ),
        "query": "example-issues-by-state",
        "chart": "example-grouped-bar",
        "transforms": [
            {
                "type": "ecSimpleTransform:aggregate",
                "config": {
                    "resultDimensions": [
                        {"from": "tenant_id"},
                        {"from": "issues", "method": "sum"},
                    ],
                    "groupBy": "tenant_id",
                },
            },
        ],
        "bindings": {"category": "tenant_id", "measures": ["issues"]},
        "span": 6,
        "order": 10,
    },
    {
        "ref": "example-issues-by-state-table",
        "title": "Issues by state",
        "description": "The same query, pivoted rather than rolled up.",
        "query": "example-issues-by-state",
        "chart": "example-matrix-table",
        "bindings": {"row": "tenant_id", "column": "state", "value": "issues"},
        "span": 6,
        "order": 20,
    },
    {
        "ref": "example-effort-share",
        "title": "Effort share",
        "description": "A second query, and a chart that knows nothing about it.",
        "query": "example-issue-metrics",
        "chart": "example-share-pie",
        "bindings": {"label": "tenant_id", "value": "total_effort"},
        "span": 6,
        "order": 30,
    },
)


def create_examples(**kwargs):
    """Create the example rows on post_migrate, once.

    A row already present is left exactly as it is, so an edited example survives the
    next deployment -- the same rule the role groups in sso.roles follow. A deleted one
    is recreated, having nothing left to leave alone.

    Args:
        **kwargs: The post_migrate signal arguments, all unused.
    """
    from .models import Chart, Dashboard, Panel, Query

    dashboard, _ = Dashboard.objects.get_or_create(
        name=HOME_DASHBOARD["name"],
        defaults={key: value for key, value in HOME_DASHBOARD.items() if key != "name"},
    )

    # The "ref" is a key within this module only, not a field: it is how a panel below
    # names the query and chart it wants. Identity in the database is the title, which
    # is what makes a second post_migrate leave an edited example alone.
    created = {}
    for group, model in ((EXAMPLE_QUERIES, Query), (EXAMPLE_CHARTS, Chart)):
        for row in group:
            fields = {key: value for key, value in row.items() if key != "ref"}
            instance, _ = model.objects.get_or_create(
                title=row["title"], defaults=fields
            )
            created[row["ref"]] = instance

    for row in EXAMPLE_PANELS:
        fields = {key: value for key, value in row.items() if key != "ref"}
        fields["query"] = created[fields["query"]]
        fields["chart"] = created[fields["chart"]]
        fields["dashboard"] = dashboard
        Panel.objects.get_or_create(title=row["title"], defaults=fields)
