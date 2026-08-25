"""Queries, charts, panels and dashboards, stored as rows rather than written as code.

They are data so that a metric can be added at runtime -- shared between installations,
or written by an assistant -- without a deployment. Queries run against the analytics
role on the ``analytics`` connection, never the one Django's own models use; see
``analytics.query``.

The split exists so that each piece can be authored once and reused:

* a **Query** is SQL and nothing else. It knows its placeholders but not what will be
  drawn from it, so the same rows can feed several panels -- and it can be tested on its
  own, which is what ``checks`` and the ``check_queries`` command are for.
* a **Chart** is appearance and nothing else. It names ``${placeholder}`` tokens instead of
  columns, so once it looks right it can be pointed at any query that has the columns.
* a **Panel** is the only place anything concrete lives: which query, which chart, the
  parameter values, the shaping transforms, and which column each placeholder reads.
* a **Dashboard** is an ordered set of panels and the width of its grid.

The one rule that makes this hold together: a query and a chart never mention each other.
Everything that joins them is a panel field.
"""

from django.core.exceptions import ValidationError
from django.db import models

from .bindings import propose
from .encoders import PrettyJSONEncoder
from .parameters import names as placeholder_names, placeholders as chart_placeholders
from .parameters import statement_count
from .query import describe
from .transforms import ALLOWED, TransformError, columns_after, validate

GRID_COLUMNS = 12
"""The dashboard grid's width, in columns. A panel's span is measured against it."""


class Query(models.Model):
    """One statement against the medallion layers, with the placeholders it declares.

    Storing the SQL rather than generating it means anything the analytics role may read
    is fair game, which makes authoring a query an analyst-level right rather than an
    editorial one -- see the permission note in ``analytics/admin.py``.
    """
    title = models.CharField("title", max_length=200)
    description = models.TextField(
        "description",
        blank=True,
        help_text="What this query returns, and what a panel is expected to do with it.",
    )

    sql = models.TextField(
        "SQL query",
        help_text=(
            "Runs read-only against the analytics schemas (silver, gold, bronze_*). "
            "Use ${name} for a bound value, ${name:identifier} / :sqlstring / :csv / "
            ":raw where the value has to become part of the statement text."
        ),
    )

    # Every placeholder needs one, because the signature below is probed by running the
    # statement with these -- a query that cannot be run alone cannot be described alone.
    parameter_defaults = models.JSONField(
        "parameter defaults",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
        help_text='A value for every ${name}, e.g. {"tenant": "project_a"}.',
    )

    # Refreshed on every save, which costs nothing: the probe wraps the statement in a
    # false filter, so PostgreSQL reports the columns without reading a row.
    signature = models.JSONField(
        "signature",
        blank=True,
        default=list,
        encoder=PrettyJSONEncoder,
        editable=False,
        help_text="The columns this query returns, as last probed.",
    )

    checks = models.JSONField(
        "checks",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
        help_text=(
            'What ./manage.py check_queries asserts, e.g. {"columns": ["day", "total"], '
            '"min_rows": 1, "not_null": ["day"]}.'
        ),
    )

    class Meta:
        verbose_name = "query"
        verbose_name_plural = "queries"
        ordering = ("title",)

    def __str__(self):
        return self.title

    @property
    def placeholders(self):
        """The ``${name}`` placeholders the SQL declares, in order of first appearance."""
        return placeholder_names(self.sql)

    @property
    def columns(self):
        """The column names this query returns, probing once if none are known yet.

        A query is often created before the tables it reads exist: the examples are
        written at post_migrate, while SQLMesh builds silver and gold afterwards, so
        their first probe cannot succeed. Rather than leave every binding dropdown empty
        until someone re-saves each query by hand, a signature that was never established
        is established the first time it is wanted. The probe costs nothing, and this
        stops as soon as it succeeds.
        """
        if not self.signature and self.pk and self.probe():
            # Written straight to the row: save() would probe a second time.
            Query.objects.filter(pk=self.pk).update(signature=self.signature)
        return [column["name"] for column in self.signature or []]

    def probe(self):
        """Refresh ``signature`` from the database, returning whether it worked.

        Failure is not an error worth raising: a query may be saved while it is still
        being written, and the columns are a convenience for the panel form rather than
        something the render depends on. A failed probe therefore leaves the last known
        signature in place -- a stale column list is more use to the binding form than
        none, and clearing it would mean a moment's trouble reaching the analytics role
        emptied every dropdown in the admin.
        """
        try:
            self.signature = describe(self.sql, self.parameter_defaults or {})
        except Exception:
            return False
        return True

    def clean(self):
        """Reject the mistakes that would otherwise surface as a failed fetch.

        Read-only execution is enforced by the transaction the query runs in, not here:
        a check on the text is guesswork, while a read-only transaction is the database's
        own answer. What this catches is the shape of the row.
        """
        if not isinstance(self.parameter_defaults, dict):
            raise ValidationError({"parameter_defaults": "Must be a JSON object."})
        if not isinstance(self.checks, dict):
            raise ValidationError({"checks": "Must be a JSON object."})

        # A statement per query: several would make the read-only transaction the only
        # thing standing between a query and a stray write. Counted outside string
        # literals and comments, so a semicolon inside one is not mistaken for a second
        # statement -- SELECT ';' is a legitimate query.
        if statement_count(self.sql) > 1:
            raise ValidationError({"sql": "Only a single statement is allowed."})

        missing = [
            name for name in self.placeholders
            if name not in (self.parameter_defaults or {})
        ]
        if missing:
            raise ValidationError({
                "parameter_defaults": (
                    "Needs a default for every placeholder; missing: "
                    + ", ".join(f"${{{name}}}" for name in missing)
                )
            })

    def save(self, *args, **kwargs):
        self.probe()
        super().save(*args, **kwargs)


class Chart(models.Model):
    """One ECharts option object, written without column names.

    The option object is the chart, whole and unmodified -- there are no fields for chart
    type or axes because the option object already says all of that, which is what lets
    an example be pasted in from the ECharts library and work. The one edit a pasted
    example needs is its data: where it named a column or carried inline numbers, it
    names a ``${placeholder}`` that a panel binds to a column.
    """
    title = models.CharField("title", max_length=200)
    description = models.TextField(
        "description",
        blank=True,
        help_text="What this chart shows, and which placeholders a panel has to fill.",
    )

    options = models.JSONField(
        "ECharts options",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
        help_text=(
            "The ECharts option object, as pasted from echarts.apache.org/examples, "
            "with ${placeholder} where it would name a column: "
            'series: [{type: "line", encode: {x: "${day}", y: "${total}"}}]. '
            "Write ${placeholder[]} to repeat a series once per bound column. Do not declare "
            "a dataset -- the panel builds it; put sorting and trimming in transforms."
        ),
    )

    # Kept out of options because the panel's shaping pipe has to run first, and a pipe
    # spliced into a pasted dataset array is what forced the old index arithmetic.
    transforms = models.JSONField(
        "transforms",
        blank=True,
        default=list,
        encoder=PrettyJSONEncoder,
        help_text=(
            "Presentation transforms applied after the panel's shaping, as a list, e.g. "
            '[{"type": "sort", "config": {"dimension": "${total}", "order": "desc"}}]. '
            f"One of: {', '.join(ALLOWED)}."
        ),
    )

    class Meta:
        verbose_name = "chart"
        verbose_name_plural = "charts"
        ordering = ("title",)

    def __str__(self):
        return self.title

    @property
    def placeholders(self):
        """Placeholder name to whether it was written as a list, across options and transforms."""
        return chart_placeholders([self.options, self.transforms])

    def clean(self):
        if not isinstance(self.options, dict):
            raise ValidationError({"options": "Must be a JSON object."})

        # The panel owns the dataset: it is what carries the query result and the two
        # transform stages, addressed by id so nothing here has to count indices.
        if "dataset" in self.options:
            raise ValidationError({
                "options": (
                    "A chart may not declare a dataset; the panel builds it. Put "
                    "sorting or trimming in the transforms field instead."
                )
            })

        try:
            validate(self.transforms)
        except TransformError as error:
            raise ValidationError({"transforms": str(error)}) from error


class Dashboard(models.Model):
    """An ordered set of panels, laid out by flow rather than by coordinates.

    A panel says how many columns wide it is and where it comes in the order; rows pack
    themselves. That is a deliberate limit: with no way to name a row and a column, a
    grid whose cells do not line up cannot be described in the first place, so there is
    nothing to validate and no layout solver to write.
    """
    name = models.SlugField(
        "name",
        max_length=100,
        unique=True,
        help_text='Identifier for this dashboard. The one called "home" is the admin index.',
    )
    title = models.CharField("title", max_length=200)
    description = models.TextField("description", blank=True)

    columns = models.PositiveSmallIntegerField(
        "grid columns",
        default=GRID_COLUMNS,
        help_text="How many columns the grid is divided into; a panel's span is out of this.",
    )

    class Meta:
        verbose_name = "dashboard"
        verbose_name_plural = "dashboards"
        ordering = ("title",)

    def __str__(self):
        return self.title or self.name


class Panel(models.Model):
    """A query and a chart, joined -- and the only place a concrete value appears.

    Everything that cannot be decided while authoring a query or a chart is decided here:
    what the placeholders are worth, how the rows are grouped, which column each placeholder
    reads, and how much of the grid the result occupies.
    """

    # Null means the panel is not on a dashboard but embedded by primary key from a
    # template, which is the only way a panel can appear on a change form.
    dashboard = models.ForeignKey(
        Dashboard,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="panels",
        help_text="Leave empty for a panel embedded in a template rather than placed on a grid.",
    )

    query = models.ForeignKey(Query, on_delete=models.PROTECT, related_name="panels")
    chart = models.ForeignKey(Chart, on_delete=models.PROTECT, related_name="panels")

    title = models.CharField(
        "title",
        max_length=200,
        blank=True,
        help_text="Heading on the panel's card. Falls back to the query's title.",
    )
    description = models.TextField("description", blank=True)

    parameters = models.JSONField(
        "parameters",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
        help_text="Values for the query's ${name} placeholders, overriding its defaults.",
    )

    transforms = models.JSONField(
        "transforms",
        blank=True,
        default=list,
        encoder=PrettyJSONEncoder,
        help_text=(
            "Shaping transforms, naming the query's own columns: which rows, grouped "
            f"how. A list; one of: {', '.join(ALLOWED)}."
        ),
    )

    bindings = models.JSONField(
        "bindings",
        blank=True,
        default=dict,
        encoder=PrettyJSONEncoder,
        help_text=(
            'Which column each of the chart\'s placeholders reads, e.g. {"day": "created_on"}. '
            "A list placeholder takes a list of columns. Proposed automatically; edit freely."
        ),
    )

    span = models.PositiveSmallIntegerField(
        "span",
        default=GRID_COLUMNS // 2,
        help_text=f"Width in grid columns, 1 to {GRID_COLUMNS}.",
    )
    height = models.PositiveIntegerField(
        "height",
        default=320,
        help_text="Height of the chart in pixels. On the panel because the same chart "
                  "is worth different heights in different places.",
    )
    order = models.PositiveIntegerField(
        "order",
        default=0,
        help_text="Position within the dashboard; rows pack in this order.",
    )

    class Meta:
        verbose_name = "panel"
        verbose_name_plural = "panels"
        ordering = ("order", "title")

    def __str__(self):
        return self.title

    @property
    def heading(self):
        """What the card shows: the panel's own title, or the query's."""
        return self.title or self.query.title

    @property
    def resolved_parameters(self):
        """The query's defaults with this panel's own values laid over them."""
        return {**(self.query.parameter_defaults or {}), **(self.parameters or {})}

    @property
    def available_columns(self):
        """The columns a placeholder may be bound to: the query's, after this panel's shaping."""
        after = columns_after(self.transforms, self.query.columns)
        by_name = {column["name"]: column for column in self.query.signature or []}
        # A column an aggregate invented is not in the signature, so it is described by
        # the name alone; its kind is unknown rather than guessed at.
        return [by_name.get(name, {"name": name, "kind": None}) for name in after]

    def propose_bindings(self):
        """What the admin offers for ``bindings`` before anyone edits them."""
        return propose(self.chart.placeholders, self.available_columns)

    def clean(self):
        if not isinstance(self.parameters, dict):
            raise ValidationError({"parameters": "Must be a JSON object."})
        if not isinstance(self.bindings, dict):
            raise ValidationError({"bindings": "Must be a JSON object."})
        if not 1 <= (self.span or 0) <= GRID_COLUMNS:
            raise ValidationError({"span": f"Must be between 1 and {GRID_COLUMNS}."})

        try:
            validate(self.transforms)
        except TransformError as error:
            raise ValidationError({"transforms": str(error)}) from error

        if not (self.query_id and self.chart_id):
            return

        missing = [
            name for name in self.query.placeholders
            if name not in self.resolved_parameters
        ]
        if missing:
            raise ValidationError({
                "parameters": (
                    "The query needs a value for: "
                    + ", ".join(f"${{{name}}}" for name in missing)
                )
            })

        # Only checked once the query has been probed; before that there is nothing to
        # check against, and refusing the panel would be refusing it for the wrong reason.
        available = {column["name"] for column in self.available_columns}
        if available:
            unknown = sorted(
                column
                for value in (self.bindings or {}).values()
                for column in (value if isinstance(value, list) else [value])
                if isinstance(column, str) and column not in available
            )
            if unknown:
                raise ValidationError({
                    "bindings": (
                        f"Not among the query's columns: {', '.join(unknown)}. "
                        f"Available: {', '.join(sorted(available))}."
                    )
                })
