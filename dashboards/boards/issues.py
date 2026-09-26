"""The example projects' issues, from the models SQLMesh builds out of them."""
import polars as pl

from dashboards import Dashboard, Panel, Text

dashboard = Dashboard(
    "Issues",
    # A query used by several panels, each shaping it on its own: here into one number.
    Panel("Open issues", "issue_metrics", "stat", transform=lambda frame: frame.select(pl.col("Open").sum())),
    Panel("Closed issues", "issue_metrics", "stat", transform=lambda frame: frame.select(pl.col("Closed").sum())),
    # A click on a bar or a slice sets that filter, and every panel reading it follows.
    Panel("Issues per project", "issue_metrics", "stacked", click="project"),
    Panel("Issues by state", "issues_by_state", "pie", click="state"),
    Panel("Changes by component", "changes_by_component", "pareto"),
    Panel("Issues opened", "issues_opened", "stacked"),
    Panel("Issues", "issues", "table", wide=True),
    Text(
        "Reading this dashboard",
        "Click a bar or a slice to show only what it stands for; click it again to show "
        "everything. The filters above say what is shown, and the address carries them, "
        "so a link sent to someone opens the same view.",
    ),
    # A video explains a metric better than a paragraph can:
    # Video("How the effort is estimated", "https://example.com/effort.mp4"),
    description="Open and closed issues across the example projects.",
)

# %% Run this cell to see the dashboard, its filters at their defaults.
dashboard
