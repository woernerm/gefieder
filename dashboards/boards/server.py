"""What the server has, and how much of it the system uses: the numbers to size it by."""
from dashboards import Dashboard, Panel

dashboard = Dashboard(
    "Server monitoring",
    Panel("Available CPU cores", "host", "stat", transform=lambda frame: frame.select("Cores")),
    Panel("Available memory", "host", "stat", transform=lambda frame: frame.select("Memory"), unit="bytes"),
    Panel("Traffic sent", "traffic", "stat", transform=lambda frame: frame.select("Sent"), unit="bytes"),
    Panel("Traffic received", "traffic", "stat", transform=lambda frame: frame.select("Received"), unit="bytes"),
    # One query, four charts: each takes the time and its own columns.
    Panel("CPU cores used", "usage", "line", transform=lambda frame: frame.select("time", "CPU cores busy")),
    Panel("Memory used", "usage", "line", transform=lambda frame: frame.select("time", "Memory used"), unit="bytes"),
    Panel("Disk throughput", "usage", "line", transform=lambda frame: frame.select("time", "Read", "Write"),
          unit="bytes/s"),
    Panel("Network throughput", "usage", "line", transform=lambda frame: frame.select("time", "Sent", "Received"),
          unit="bytes/s"),
    Panel("Storage used", "storage", "line", unit="bytes"),
    Panel("Top monitored tables", "top_tables", "table", wide=True),
    Panel("Top query runs and cost", "top_queries", "table", wide=True),
    description="CPU, memory, disk, network and storage, sampled every minute.",
    refresh=60,
)

# %% Run this cell to see the dashboard, its filters at their defaults.
dashboard
