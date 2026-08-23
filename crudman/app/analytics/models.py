"""Panel definitions: a chart, its SQL and where it appears, stored as rows.

A panel is data rather than code so that it can be created at runtime -- shared between
installations, or written by an assistant -- without a deployment. The query runs against
the analytics role on the ``analytics`` connection, never the one Django's own
models use;
see ``analytics.query``.
"""

from django.core.exceptions import ValidationError
from django.db import models

from .encoders import PrettyJSONEncoder


class Panel(models.Model):
    """One chart: a SQL query against the medallion layers and an ECharts option object.

    The option object is the chart, whole and unmodified -- there are no fields for chart
    type or axes because the option object already says all of that, which is what lets an
    example be pasted in from the ECharts library and work. The query result reaches it as
    ``dataset`` 0; see analytics.charts.

    The query is stored, not generated, so anything the analytics role may read is fair
    game. That makes authoring a panel an analyst-level right rather than an editorial
    one -- see the permission note in ``analytics/admin.py``.
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

    # ECharts is configured by one nested option object, so storing it whole is what
    # lets an example from the library be pasted in and work unchanged.
    options = models.JSONField(
        "ECharts options",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
        help_text=(
            "The ECharts option object, as pasted from echarts.apache.org/examples. "
            "The query result is injected as dataset 0, so refer to its columns by name: "
            'series: [{type: "line", encode: {x: "day", y: "total"}}]. Group and filter '
            "with dataset.transform. A transform naming no source reads the query result; "
            "one that spells out fromDatasetIndex counts your own datasets from 1."
        ),
    )

    # Bound as query parameters, never interpolated, so a default is a value and not a
    # fragment of SQL.
    parameters = models.JSONField(
        "parameters",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
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
