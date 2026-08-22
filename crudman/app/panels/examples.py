"""Example panels, created once so a fresh system has something to look at.

They query the gold and silver models the SQLMesh project ships, which the example
tenants fill, and between them show the three things worth copying: the plain
"label, value" shape that needs no configuration at all, a bound parameter, and an
ECharts option object reaching past what the model's own fields name.

These are ordinary rows: edit one in the admin and the edit stands, deployments
included. Deleting one is not permanent, though -- the next migrate finds it missing and
puts it back. To be rid of an example for good, untick its dashboard flag or empty its
query; both are edits and both survive.
"""

EXAMPLE_PANELS = (
    {
        "slug": "example-issues-by-tenant",
        "title": "Issues by tenant",
        "description": (
            "The simplest shape a panel can have: the first column labels the bars and "
            "every other column becomes a series, so no chart configuration is needed."
        ),
        "sql": (
            "SELECT tenant_id, open_issues, closed_issues\n"
            "FROM gold.issue_metrics\n"
            "ORDER BY tenant_id"
        ),
        "chart_type": "bar",
        "on_dashboard": True,
    },
    {
        "slug": "example-effort-share",
        "title": "Effort share",
        "description": (
            "A pie takes its slices from the first value column. The ECharts options "
            "below show how to reach a setting the panel's own fields do not name."
        ),
        "sql": (
            "SELECT tenant_id, total_effort\n"
            "FROM gold.issue_metrics\n"
            "WHERE total_effort IS NOT NULL\n"
            "ORDER BY total_effort DESC"
        ),
        "chart_type": "pie",
        "on_dashboard": True,
        "options": {"series": [{"radius": ["45%", "70%"]}]},
    },
    {
        "slug": "example-open-issues-of-one-tenant",
        "title": "Open issues of one tenant",
        "description": (
            "Parameters are bound, never pasted into the statement. Change the value "
            "under Parameters to point this panel at another tenant."
        ),
        "sql": (
            "SELECT title, effort\n"
            "FROM silver.issues\n"
            "WHERE tenant_id = %(tenant)s AND state = 'open'\n"
            "ORDER BY effort DESC NULLS LAST\n"
            "LIMIT 10"
        ),
        "parameters": {"tenant": "project_a"},
        "chart_type": "table",
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
