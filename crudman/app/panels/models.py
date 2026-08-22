"""Panel definitions: a chart, its SQL and where it appears, stored as rows.

A panel is data rather than code so that it can be created at runtime -- shared between
installations, or written by an assistant -- without a deployment. The query runs against
the analytics role on the ``panels`` connection, never the one Django's own models use;
see ``panels.query``.
"""

from django.core.exceptions import ValidationError
from django.db import models


class Panel(models.Model):
    """One chart: a SQL query against the medallion layers plus how to plot it.

    The query is stored, not generated, so anything the analytics role may read is fair
    game. That makes authoring a panel an analyst-level right rather than an editorial
    one -- see the permission note in ``panels/admin.py``.
    """

    # The slug is what a template names to place the panel, so it is the identifier that
    # has to stay stable; renaming one orphans every page that embeds it.
    slug = models.SlugField(
        "slug",
        max_length=100,
        unique=True,
        help_text="Identifier used to embed the panel, e.g. open-issues-by-tenant.",
    )
    title = models.CharField(
        "title",
        max_length=200,
        help_text="Heading shown on the panel's card.",
    )

    sql = models.TextField(
        "SQL query",
        help_text=(
            "Runs read-only against the analytics schemas (silver, gold, bronze_*). "
            "Use %(name)s placeholders for parameters; never format values into the text."
        ),
    )

    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    SCATTER = "scatter"
    TABLE = "table"
    CHART_TYPES = [
        (BAR, "Bar"),
        (LINE, "Line"),
        (PIE, "Pie"),
        (SCATTER, "Scatter"),
        (TABLE, "Table"),
    ]

    chart_type = models.CharField(
        "chart type",
        max_length=20,
        choices=CHART_TYPES,
        default=BAR,
    )

    # Which columns of the result become the axes. Left blank, the first column is the
    # category axis and every remaining numeric column becomes a series, which is what
    # makes a plain "SELECT label, value FROM ..." work with no further configuration.
    x_field = models.CharField(
        "category column",
        max_length=100,
        blank=True,
        help_text="Result column for the category axis. Blank: the first column.",
    )
    y_fields = models.CharField(
        "value columns",
        max_length=500,
        blank=True,
        help_text="Comma-separated result columns to plot. Blank: all remaining columns.",
    )

    # ECharts is configured by one nested option object, so an escape hatch that is
    # merged over the generated one covers every setting this model does not name
    # without growing a field per ECharts feature.
    options = models.JSONField(
        "ECharts options",
        blank=True,
        default=dict,
        help_text="Merged over the generated ECharts option object. Leave as {} for none.",
    )

    # Bound as query parameters, never interpolated, so a default is a value and not a
    # fragment of SQL.
    parameters = models.JSONField(
        "parameters",
        blank=True,
        default=dict,
        help_text='Default values for the %(name)s placeholders, e.g. {"tenant": "project_a"}.',
    )

    description = models.TextField("description", blank=True)

    # The dashboard is the one page that cannot name its panels in a template, being
    # shared by every installation; this flag is how a panel gets onto it.
    on_dashboard = models.BooleanField(
        "show on dashboard",
        default=False,
        help_text="Place this panel on the admin dashboard.",
    )

    height = models.PositiveIntegerField(
        "height",
        default=320,
        help_text="Height of the chart in pixels.",
    )

    class Meta:
        verbose_name = "panel"
        verbose_name_plural = "panels"
        ordering = ("title",)

    def __str__(self):
        return self.title or self.slug

    def clean(self):
        """Reject the mistakes that would otherwise surface as a failed fetch.

        Read-only execution is enforced by the transaction the query runs in, not here:
        a check on the text is guesswork, while a read-only transaction is the database's
        own answer. What this catches is the shape of the row.
        """
        if not isinstance(self.options, dict):
            raise ValidationError({"options": "Must be a JSON object."})
        if not isinstance(self.parameters, dict):
            raise ValidationError({"parameters": "Must be a JSON object."})
        # A statement per panel: several would make the read-only transaction the only
        # thing standing between a panel and a stray write.
        if self.sql and self.sql.strip().rstrip(";").count(";"):
            raise ValidationError({"sql": "Only a single statement is allowed."})

    def value_columns(self, columns):
        """The result columns to plot, given the columns the query returned.

        Args:
            columns: Column names of the result, in order.

        Returns:
            The names listed in ``y_fields``, or every column except the category one.
        """
        if self.y_fields.strip():
            return [name.strip() for name in self.y_fields.split(",") if name.strip()]
        return [name for name in columns if name != self.category_column(columns)]

    def category_column(self, columns):
        """The result column for the category axis.

        Args:
            columns: Column names of the result, in order.

        Returns:
            ``x_field`` when set, otherwise the first column; None for an empty result.
        """
        if self.x_field.strip():
            return self.x_field.strip()
        return columns[0] if columns else None
