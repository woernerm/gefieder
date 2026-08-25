"""Split the old Panel into Query, Chart, Panel and Dashboard.

Existing rows are carried over rather than dropped. A panel's SQL becomes a Query, its
option object a Chart, and the panel keeps its slug and points at both -- so an embedded
panel keeps rendering under the name a template already uses.

Two conversions are worth naming:

* The old option objects name columns literally, and slot resolution leaves a string that
  is not a slot exactly as it is, so they keep working untouched. Slots are opt-in.
* The old ``dataset`` array is lifted into the chart's transforms field, because the panel
  now builds the dataset itself. A ``datasetIndex`` on a series is dropped with it: the
  stages are addressed by id now, and a leftover index would point at the wrong one.

A panel that asked for the plain table with ``{"table": true}`` cannot be converted
automatically -- the matrix table it becomes needs long-format rows and bindings that
only its author can choose -- so it is pointed at a matrix chart and says so in its
description rather than being deleted.
"""

import analytics.encoders
from django.db import migrations, models
import django.db.models.deletion


MATRIX_TABLE_OPTIONS = {
    "tooltip": {},
    "matrix": {"x": {"label": {"show": True}}, "y": {"label": {"show": True}}},
    "series": [
        {
            "type": "scatter",
            "coordinateSystem": "matrix",
            "symbolSize": 0,
            "encode": {"x": "${column}", "y": "${row}", "value": "${value}"},
            "label": {"show": True, "position": "inside"},
        },
    ],
}

TABLE_NOTE = (
    "Converted from the old table panel. ECharts draws a table on its matrix "
    "coordinate system, which needs one row per cell: give the query a long-format "
    "result and bind ${row}, ${column} and ${value}."
)


def split(apps, schema_editor):
    """Turn every old Panel row into a Query, a Chart and a Panel pointing at both."""
    Panel = apps.get_model("analytics", "Panel")
    Query = apps.get_model("analytics", "Query")
    Chart = apps.get_model("analytics", "Chart")
    Dashboard = apps.get_model("analytics", "Dashboard")

    home = None

    for panel in Panel.objects.all():
        query = Query.objects.create(
            slug=f"{panel.slug}-query"[:100],
            title=panel.title,
            description=panel.description,
            sql=panel.sql,
            parameter_defaults=panel.parameters or {},
        )

        options = dict(panel.options or {})
        transforms = []

        # The panel owns the dataset now, so a chart may not carry one; what the old
        # array was actually for was its transforms.
        declared = options.pop("dataset", None)
        for entry in declared if isinstance(declared, list) else [declared]:
            if isinstance(entry, dict) and entry.get("transform"):
                found = entry["transform"]
                transforms.extend(found if isinstance(found, list) else [found])

        series = options.get("series")
        if isinstance(series, list):
            options["series"] = [
                {key: value for key, value in entry.items() if key != "datasetIndex"}
                if isinstance(entry, dict) else entry
                for entry in series
            ]

        was_table = bool(options.pop("table", False))
        if was_table:
            options = dict(MATRIX_TABLE_OPTIONS)
            transforms = []

        chart = Chart.objects.create(
            slug=f"{panel.slug}-chart"[:100],
            title=panel.title,
            description=panel.description,
            options=options,
            transforms=transforms,
        )

        if panel.on_dashboard and home is None:
            home, _ = Dashboard.objects.get_or_create(
                slug="home",
                defaults={"title": "Overview", "columns": 12},
            )

        panel.query = query
        panel.chart = chart
        panel.dashboard = home if panel.on_dashboard else None
        if was_table:
            panel.description = (
                f"{TABLE_NOTE}\n\n{panel.description}".strip()
            )
        panel.save()


def unsplit(apps, schema_editor):
    """Fold the query and chart back into the panel, so the split can be reversed."""
    Panel = apps.get_model("analytics", "Panel")

    for panel in Panel.objects.select_related("query", "chart"):
        if panel.query_id:
            panel.sql = panel.query.sql
            panel.parameters = panel.query.parameter_defaults or {}
        if panel.chart_id:
            options = dict(panel.chart.options or {})
            if panel.chart.transforms:
                options["dataset"] = [{"transform": panel.chart.transforms}]
            panel.options = options
        panel.on_dashboard = panel.dashboard_id is not None
        panel.save()


class Migration(migrations.Migration):

    dependencies = [("analytics", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Dashboard",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(help_text='Identifier for this dashboard. The one called "home" is the admin index.', max_length=100, unique=True, verbose_name="slug")),
                ("title", models.CharField(max_length=200, verbose_name="title")),
                ("description", models.TextField(blank=True, verbose_name="description")),
                ("columns", models.PositiveSmallIntegerField(default=12, help_text="How many columns the grid is divided into; a panel's span is out of this.", verbose_name="grid columns")),
            ],
            options={"verbose_name": "dashboard", "verbose_name_plural": "dashboards", "ordering": ("title",)},
        ),
        migrations.CreateModel(
            name="Query",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(help_text="Identifier for this query, e.g. issues-by-tenant.", max_length=100, unique=True, verbose_name="slug")),
                ("title", models.CharField(max_length=200, verbose_name="title")),
                ("description", models.TextField(blank=True, help_text="What this query returns, and what a panel is expected to do with it.", verbose_name="description")),
                ("sql", models.TextField(help_text="Runs read-only against the analytics schemas (silver, gold, bronze_*). Use ${name} for a bound value, ${name:identifier} / :sqlstring / :csv / :raw where the value has to become part of the statement text.", verbose_name="SQL query")),
                ("parameter_defaults", models.JSONField(blank=True, default=dict, encoder=analytics.encoders.PrettyJSONEncoder, help_text='A value for every ${name}, e.g. {"tenant": "project_a"}.', verbose_name="parameter defaults")),
                ("signature", models.JSONField(blank=True, default=list, editable=False, encoder=analytics.encoders.PrettyJSONEncoder, help_text="The columns this query returns, as last probed.", verbose_name="signature")),
                ("checks", models.JSONField(blank=True, default=dict, encoder=analytics.encoders.PrettyJSONEncoder, help_text='What ./manage.py check_queries asserts, e.g. {"columns": ["day", "total"], "min_rows": 1, "not_null": ["day"]}.', verbose_name="checks")),
            ],
            options={"verbose_name": "query", "verbose_name_plural": "queries", "ordering": ("title",)},
        ),
        migrations.CreateModel(
            name="Chart",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(help_text="Identifier for this chart, e.g. grouped-bar.", max_length=100, unique=True, verbose_name="slug")),
                ("title", models.CharField(max_length=200, verbose_name="title")),
                ("description", models.TextField(blank=True, help_text="What this chart shows, and which slots a panel has to fill.", verbose_name="description")),
                ("options", models.JSONField(blank=True, default=dict, encoder=analytics.encoders.PrettyJSONEncoder, help_text='The ECharts option object, as pasted from echarts.apache.org/examples, with ${slot} where it would name a column: series: [{type: "line", encode: {x: "${day}", y: "${total}"}}]. Write ${slot[]} to repeat a series once per bound column. Do not declare a dataset -- the panel builds it; put sorting and trimming in transforms.', verbose_name="ECharts options")),
                ("transforms", models.JSONField(blank=True, default=list, encoder=analytics.encoders.PrettyJSONEncoder, help_text='Presentation transforms applied after the panel\'s shaping, as a list, e.g. [{"type": "sort", "config": {"dimension": "${total}", "order": "desc"}}]. One of: filter, sort, ecSimpleTransform:aggregate.', verbose_name="transforms")),
            ],
            options={"verbose_name": "chart", "verbose_name_plural": "charts", "ordering": ("title",)},
        ),

        # Added nullable so the rows can be filled in before the constraint lands.
        migrations.AddField(
            model_name="panel",
            name="query",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="panels", to="analytics.query"),
        ),
        migrations.AddField(
            model_name="panel",
            name="chart",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="panels", to="analytics.chart"),
        ),
        migrations.AddField(
            model_name="panel",
            name="dashboard",
            field=models.ForeignKey(blank=True, help_text="Leave empty for a panel embedded in a template rather than placed on a grid.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="panels", to="analytics.dashboard"),
        ),
        migrations.AddField(
            model_name="panel",
            name="transforms",
            field=models.JSONField(blank=True, default=list, encoder=analytics.encoders.PrettyJSONEncoder, help_text="Shaping transforms, naming the query's own columns: which rows, grouped how. A list; one of: filter, sort, ecSimpleTransform:aggregate.", verbose_name="transforms"),
        ),
        migrations.AddField(
            model_name="panel",
            name="bindings",
            field=models.JSONField(blank=True, default=dict, encoder=analytics.encoders.PrettyJSONEncoder, help_text='Which column each of the chart\'s slots reads, e.g. {"day": "created_on"}. A list slot takes a list of columns. Proposed automatically; edit freely.', verbose_name="bindings"),
        ),
        migrations.AddField(
            model_name="panel",
            name="span",
            field=models.PositiveSmallIntegerField(default=6, help_text="Width in grid columns, 1 to 12.", verbose_name="span"),
        ),
        migrations.AddField(
            model_name="panel",
            name="order",
            field=models.PositiveIntegerField(default=0, help_text="Position within the dashboard; rows pack in this order.", verbose_name="order"),
        ),

        migrations.RunPython(split, unsplit),

        migrations.RemoveField(model_name="panel", name="sql"),
        migrations.RemoveField(model_name="panel", name="options"),
        migrations.RemoveField(model_name="panel", name="parameters"),
        migrations.RemoveField(model_name="panel", name="on_dashboard"),

        # Re-added with a different meaning: the old field held a query's defaults, the
        # new one holds the values this panel lays over them.
        migrations.AddField(
            model_name="panel",
            name="parameters",
            field=models.JSONField(blank=True, default=dict, encoder=analytics.encoders.PrettyJSONEncoder, help_text="Values for the query's ${name} placeholders, overriding its defaults.", verbose_name="parameters"),
        ),

        migrations.AlterField(
            model_name="panel",
            name="query",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="panels", to="analytics.query"),
        ),
        migrations.AlterField(
            model_name="panel",
            name="chart",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="panels", to="analytics.chart"),
        ),
        migrations.AlterField(
            model_name="panel",
            name="slug",
            field=models.SlugField(help_text="Identifier used to embed this panel in a template.", max_length=100, unique=True, verbose_name="slug"),
        ),
        migrations.AlterField(
            model_name="panel",
            name="title",
            field=models.CharField(blank=True, help_text="Heading on the panel's card. Falls back to the query's title.", max_length=200, verbose_name="title"),
        ),
        migrations.AlterField(
            model_name="panel",
            name="height",
            field=models.PositiveIntegerField(default=320, help_text="Height of the chart in pixels. On the panel because the same chart is worth different heights in different places.", verbose_name="height"),
        ),
        migrations.AlterModelOptions(
            name="panel",
            options={"ordering": ("order", "title"), "verbose_name": "panel", "verbose_name_plural": "panels"},
        ),
    ]
