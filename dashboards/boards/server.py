"""What the server has, and how much of it the system uses: the numbers to size it by."""
from dashboards import Dashboard, Panel

dashboard = Dashboard(
    "Server monitoring",
    Panel("Available CPU cores", "host", "stat", transform=lambda frame: frame.select("Cores")),
    Panel("Available memory", "host", "stat", transform=lambda frame: frame.select("Memory"), unit="bytes"),
    Panel("Traffic sent", "traffic", "stat", transform=lambda frame: frame.select("Sent"), unit="bytes"),
    Panel("Traffic received", "traffic", "stat", transform=lambda frame: frame.select("Received"), unit="bytes"),
    Panel("CPU cores used", "cpu", "line"),
    Panel("Memory used", "memory", "line", unit="bytes"),
    Panel("Disk throughput", "disk", "line", unit="bytes/s"),
    Panel("Network throughput", "network", "line", unit="bytes/s"),
    Panel("Storage used", "storage", "line", unit="bytes"),
    Panel("Top monitored tables", "top_tables", "table", wide=True),
    Panel("Top query runs and cost", "top_queries", "table", wide=True),
    description="CPU, memory, disk, network and storage, sampled every minute.",
    refresh=60,
)

# %% Run this cell to see the dashboard, its filters at their defaults.
dashboard
