"""Example panels, created once so a fresh system has something to look at.

They query the gold and silver models the SQLMesh project ships, which the example
tenants fill, and between them show the three things worth copying: an option object
taken straight from the ECharts library with its inline data swapped for the query's
columns, a transform doing the grouping ECharts can do without help, and the plain table.

These are ordinary rows: edit one in the admin and the edit stands, deployments included.
Deleting one is not permanent, though -- the next migrate finds it missing and puts it
back. To be rid of an example for good, untick its dashboard flag or empty its query;
both are edits and both survive.
"""

EXAMPLE_PANELS = (
    {
        "slug": "example-issues-by-tenant",
        "title": "Issues by tenant",
        "description": (
            "A bar chart the way the ECharts library writes one, with encode naming the "
            "query's columns where the example had a list of numbers."
        ),
        "sql": (
            "SELECT tenant_id, open_issues, closed_issues\n"
            "FROM gold.issue_metrics\n"
            "ORDER BY tenant_id"
        ),
        "options": {
            "tooltip": {
                "trigger": "axis",
            },
            "legend": {
                "bottom": 0,
            },
            "grid": {
                "left": 0,
                "right": 16,
                "top": 16,
                "bottom": 32,
                "containLabel": True,
            },
            "xAxis": {
                "type": "category",
            },
            "yAxis": {
                "type": "value",
            },
            "series": [
                {
                    "type": "bar",
                    "encode": {"x": "tenant_id", "y": "open_issues"},
                },
                {
                    "type": "bar",
                    "encode": {"x": "tenant_id", "y": "closed_issues"},
                },
            ],
        },
        "on_dashboard": True,
    },
    {
        "slug": "example-effort-share",
        "title": "Effort share",
        "description": (
            "A pie, and a dataset transform doing the sorting. A transform that names no "
            "source reads the query result, so it can be pasted in as the library writes it."
        ),
        "sql": (
            "SELECT tenant_id, total_effort\n"
            "FROM gold.issue_metrics\n"
            "WHERE total_effort IS NOT NULL"
        ),
        "options": {
            "tooltip": {
                "trigger": "item",
            },
            "legend": {
                "bottom": 0,
            },
            "dataset": [
                {
                    "transform": {
                        "type": "sort",
                        "config": {"dimension": "total_effort", "order": "desc"},
                    },
                },
            ],
            "series": [
                {
                    "type": "pie",
                    "radius": ["45%", "70%"],
                    "center": ["50%", "45%"],
                    "datasetIndex": 1,
                    "encode": {"itemName": "tenant_id", "value": "total_effort"},
                },
            ],
        },
        "on_dashboard": True,
    },
    {
        "slug": "example-open-issues-of-one-tenant",
        "title": "Open issues of one tenant",
        "description": (
            "Parameters are bound, never pasted into the statement. Change the value "
            "under Parameters to point this panel at another tenant. ECharts has no "
            'table, so this one asks for the rows themselves with {"table": true}.'
        ),
        "sql": (
            "SELECT title, effort\n"
            "FROM silver.issues\n"
            "WHERE tenant_id = %(tenant)s AND state = 'open'\n"
            "ORDER BY effort DESC NULLS LAST\n"
            "LIMIT 10"
        ),
        "parameters": {"tenant": "project_a"},
        "options": {
            "table": True,
        },
        "on_dashboard": False,
    },
)


def create_example_panels(**kwargs):
    """Create the example panels on post_migrate, once.

    A panel already present is left exactly as it is, so an edited example survives the
    next deployment -- the same rule the role groups in sso.roles follow. A deleted one
    is recreated, having nothing left to leave alone.

    Args:
        **kwargs: The post_migrate signal arguments, all unused.
    """
    from .models import Panel

    for panel in EXAMPLE_PANELS:
        fields = {key: value for key, value in panel.items() if key != "slug"}
        Panel.objects.get_or_create(slug=panel["slug"], defaults=fields)
