"""Unit tests for the query/chart/panel split and its ECharts mapping.

The guards that matter -- read-only execution and the analytics role's own grants -- need
a real database and are covered by tests/test_panels.py in the integration suite. So is
whether an example's SQL actually returns the columns its bindings name.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.cache import caches
from django.core.exceptions import ValidationError
from django.template.loader import render_to_string
from django.test import TestCase

from . import bindings, parameters, transforms
from .charts import ChartBuildError, build
from .encoders import PrettyJSONEncoder
from .examples import EXAMPLE_CHARTS, EXAMPLE_PANELS, EXAMPLE_QUERIES, create_examples
from .admin import PanelInline
from .components import DashboardComponent
from .models import Chart, Dashboard, Panel, Query
from .query import RESULT_CACHE, run_shared


class ParameterSyntaxTests(TestCase):
    """``${name}`` binds; ``${name:format}`` becomes statement text."""

    def test_a_plain_placeholder_is_bound_not_interpolated(self):
        statement, values = parameters.bind("SELECT ${t}", {"t": "project_a"})

        self.assertEqual(statement, "SELECT %(t)s")
        self.assertEqual(values, {"t": "project_a"})

    def test_an_identifier_is_quoted_by_psycopg(self):
        statement, values = parameters.bind("SELECT ${c:identifier}", {"c": "gold.m"})

        self.assertEqual(statement, 'SELECT "gold"."m"')
        self.assertEqual(values, {})

    def test_a_csv_list_escapes_every_item(self):
        statement, _ = parameters.bind("IN (${x:csv})", {"x": ["a", "b'c"]})

        self.assertEqual(statement, "IN ('a', 'b''c')")

    def test_a_missing_value_is_refused(self):
        with self.assertRaises(parameters.ParameterError):
            parameters.bind("SELECT ${nope}", {})

    def test_an_unknown_format_is_refused(self):
        with self.assertRaises(parameters.ParameterError):
            parameters.bind("SELECT ${x:sqlinject}", {"x": 1})

    def test_a_literal_percent_survives_alongside_a_bound_value(self):
        """psycopg reads the statement for placeholders of its own once anything is bound,
        and rejects every other percent sign, so a LIKE pattern has to be doubled."""
        from psycopg._queries import _split_query

        statement, bound = parameters.bind(
            "SELECT * FROM t WHERE title LIKE '%' || ${x} || '%'", {"x": "a"}
        )

        self.assertEqual(statement,
                         "SELECT * FROM t WHERE title LIKE '%%' || %(x)s || '%%'")
        _split_query(statement.encode(), "utf-8")  # raises if psycopg disagrees

    def test_a_percent_inside_an_interpolated_value_is_escaped_too(self):
        statement, _ = parameters.bind(
            "SELECT ${v:sqlstring}, ${x}", {"v": "50%", "x": 1}
        )

        self.assertEqual(statement, "SELECT '50%%', %(x)s")

    def test_nothing_is_escaped_when_nothing_is_bound(self):
        """With no parameters psycopg never scans the text, so doubling would be wrong."""
        statement, bound = parameters.bind("SELECT * FROM t WHERE a LIKE '%'", {})

        self.assertEqual(statement, "SELECT * FROM t WHERE a LIKE '%'")
        self.assertEqual(bound, {})

    def test_a_semicolon_inside_a_literal_is_not_a_second_statement(self):
        self.assertEqual(parameters.statement_count("SELECT ';'"), 1)
        self.assertEqual(parameters.statement_count("SELECT \"we;ird\" FROM t"), 1)
        self.assertEqual(parameters.statement_count("SELECT 1 -- a ; comment"), 1)
        self.assertEqual(parameters.statement_count("SELECT 1 /* a ; b */"), 1)

    def test_a_real_second_statement_is_still_counted(self):
        self.assertEqual(parameters.statement_count("SELECT 'a'; SELECT 'b'"), 2)
        self.assertEqual(parameters.statement_count("SELECT 1;"), 1)

    def test_a_slot_is_only_recognised_as_a_whole_string(self):
        """A formatter mentioning a slot must survive resolution as the author wrote it."""
        resolved = parameters.resolve(
            {"encode": {"x": "${cat}"}, "label": {"formatter": "total ${cat}"}},
            {"cat": "team"},
        )

        self.assertEqual(resolved["encode"]["x"], "team")
        self.assertEqual(resolved["label"]["formatter"], "total ${cat}")

    def test_a_list_slot_is_reported_as_one(self):
        self.assertEqual(
            parameters.slots({"encode": {"x": "${a}", "y": "${b[]}"}}),
            {"a": False, "b": True},
        )


class TransformTests(TestCase):
    """The closed vocabulary, and what it does to the column names."""

    def test_filter_and_sort_leave_the_columns_alone(self):
        pipe = [{"type": "sort", "config": {"dimension": "a"}}]

        self.assertEqual(transforms.columns_after(pipe, ["a", "b"]), ["a", "b"])

    def test_aggregate_replaces_them_with_its_result_dimensions(self):
        pipe = [{
            "type": transforms.AGGREGATE,
            "config": {
                "resultDimensions": [{"from": "team"}, {"from": "effort", "method": "sum"}],
                "groupBy": "team",
            },
        }]

        self.assertEqual(transforms.columns_after(pipe, ["team", "effort", "x"]),
                         ["team", "effort"])

    def test_an_unknown_transform_is_refused(self):
        with self.assertRaises(transforms.TransformError):
            transforms.validate([{"type": "ecStat:regression"}])

    def test_aggregate_needs_a_group_by(self):
        with self.assertRaises(transforms.TransformError):
            transforms.validate([{"type": transforms.AGGREGATE, "config": {}}])


class BindingProposalTests(TestCase):
    """What the panel form offers before anyone edits it."""

    COLUMNS = [
        {"name": "tenant_id", "kind": "category"},
        {"name": "open_issues", "kind": "number"},
        {"name": "closed_issues", "kind": "number"},
    ]

    def test_an_exact_name_wins(self):
        options = {"series": [{"encode": {"x": "${tenant_id}"}}]}

        proposal = bindings.propose(parameters.slots(options), self.COLUMNS, options)

        self.assertEqual(proposal["tenant_id"], "tenant_id")

    def test_a_list_slot_collects_every_column_of_its_kind(self):
        options = {"series": [{"encode": {"x": "${category}", "y": "${measures[]}"}}]}

        proposal = bindings.propose(parameters.slots(options), self.COLUMNS, options)

        self.assertEqual(proposal["category"], "tenant_id")
        self.assertEqual(proposal["measures"], ["open_issues", "closed_issues"])

    def test_a_matrix_series_wants_labels_on_both_axes(self):
        """On a grid y is the measure; on a matrix it is a row header."""
        options = {"series": [{"coordinateSystem": "matrix",
                               "encode": {"x": "${c}", "y": "${r}", "value": "${v}"}}]}

        self.assertEqual(
            bindings.expected_kinds(options),
            {"c": "category", "r": "category", "v": "number"},
        )

    def test_each_slot_gets_its_own_column_where_one_is_free(self):
        options = {"series": [{"coordinateSystem": "matrix",
                               "encode": {"x": "${c}", "y": "${r}", "value": "${v}"}}]}
        columns = [
            {"name": "state", "kind": "category"},
            {"name": "tenant_id", "kind": "category"},
            {"name": "issues", "kind": "number"},
        ]

        proposal = bindings.propose(parameters.slots(options), columns, options)

        self.assertEqual(proposal["v"], "issues")
        self.assertNotEqual(proposal["c"], proposal["r"])

    def test_nothing_is_proposed_when_there_are_no_columns(self):
        options = {"series": [{"encode": {"x": "${a}"}}]}

        self.assertEqual(bindings.propose(parameters.slots(options), [], options), {})


class DatasetTests(TestCase):
    """What build() assembles; the option object itself is ECharts' business, not ours."""

    def setUp(self):
        self.query = Query(slug="q", title="Q", sql="SELECT 1")
        self.chart = Chart(
            slug="c", title="C",
            options={"series": [{"type": "bar", "encode": {"x": "${cat}", "y": "${val}"}}]},
        )
        self.panel = Panel(slug="p", query=self.query, chart=self.chart,
                           bindings={"cat": "tenant", "val": "open"})

    def test_the_query_result_becomes_the_first_stage(self):
        option = build(self.panel, ["tenant", "open"], [("a", 1), ("b", 2)])

        self.assertEqual(option["dataset"][0]["id"], "query")
        self.assertEqual(option["dataset"][0]["dimensions"], ["tenant", "open"])
        self.assertEqual(option["dataset"][0]["source"], [["a", 1], ["b", 2]])

    def test_the_stages_are_chained_by_id_not_by_index(self):
        self.panel.transforms = [{"type": "sort", "config": {"dimension": "open"}}]
        self.chart.transforms = [{"type": "filter", "config": {"dimension": "open"}}]

        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual([stage["id"] for stage in option["dataset"]],
                         ["query", "shaped", "chart"])
        self.assertEqual(option["dataset"][1]["fromDatasetId"], "query")
        self.assertEqual(option["dataset"][2]["fromDatasetId"], "shaped")
        self.assertEqual(option["series"][0]["datasetId"], "chart")

    def test_a_stage_that_is_not_needed_is_absent(self):
        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual([stage["id"] for stage in option["dataset"]], ["query"])
        self.assertEqual(option["series"][0]["datasetId"], "query")

    def test_slots_are_replaced_by_the_bound_columns(self):
        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual(option["series"][0]["encode"], {"x": "tenant", "y": "open"})

    def test_a_panels_transforms_name_columns_and_are_not_resolved(self):
        """The panel knows the query, so its pipe carries real names already."""
        self.panel.transforms = [{"type": "sort", "config": {"dimension": "open"}}]

        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual(option["dataset"][1]["transform"][0]["config"]["dimension"],
                         "open")

    def test_a_charts_transforms_are_resolved(self):
        """The chart does not, so its pipe names slots."""
        self.chart.transforms = [{"type": "sort", "config": {"dimension": "${val}"}}]

        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual(option["dataset"][1]["transform"][0]["config"]["dimension"],
                         "open")

    def test_a_list_slot_becomes_one_series_per_column(self):
        self.chart.options = {
            "series": [{"type": "bar", "encode": {"x": "${cat}", "y": "${measures[]}"}}]
        }
        self.panel.bindings = {"cat": "tenant", "measures": ["open", "closed"]}

        option = build(self.panel, ["tenant", "open", "closed"], [("a", 1, 2)])

        self.assertEqual(len(option["series"]), 2)
        self.assertEqual([entry["name"] for entry in option["series"]],
                         ["open", "closed"])
        self.assertEqual([entry["encode"]["y"] for entry in option["series"]],
                         ["open", "closed"])

    def test_an_unbound_list_slot_keeps_its_series(self):
        """Dropping it would render an empty chart and report nothing."""
        self.chart.options = {
            "series": [{"type": "bar", "encode": {"x": "${cat}", "y": "${measures[]}"}}]
        }
        self.panel.bindings = {"cat": "tenant"}

        option = build(self.panel, ["tenant"], [("a",)])

        self.assertEqual(len(option["series"]), 1)
        self.assertEqual(option["series"][0]["encode"]["y"], "${measures[]}")

    def test_options_that_are_not_an_option_object_are_reported(self):
        """They are author-written JSON, so this belongs on the card, not in a 500."""
        self.chart.options = {"series": "not a list"}
        with self.assertRaises(ChartBuildError):
            build(self.panel, ["tenant"], [("a",)])

        self.chart.options = {"series": ["not an object"]}
        with self.assertRaises(ChartBuildError):
            build(self.panel, ["tenant"], [("a",)])

    def test_a_series_may_name_its_own_stage(self):
        self.chart.options = {"series": [{"type": "bar", "datasetId": "query"}]}
        self.panel.transforms = [{"type": "sort", "config": {"dimension": "open"}}]

        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual(option["series"][0]["datasetId"], "query")

    def test_an_unbound_slot_is_left_in_place(self):
        """It has to fail where it can be seen, not quietly plot nothing."""
        self.panel.bindings = {"cat": "tenant"}

        option = build(self.panel, ["tenant", "open"], [("a", 1)])

        self.assertEqual(option["series"][0]["encode"]["y"], "${val}")

    def test_values_json_cannot_carry_are_converted(self):
        option = build(self.panel, ["d", "n"], [(date(2026, 1, 2), Decimal("1.5"))])

        self.assertEqual(option["dataset"][0]["source"], [["2026-01-02", 1.5]])

    def test_building_shall_not_mutate_the_stored_options(self):
        """build() runs on a model instance the request still holds."""
        options = {"series": [{"type": "bar", "encode": {"x": "${cat}"}}]}
        self.chart.options = options

        build(self.panel, ["tenant"], [("a",)])

        self.assertNotIn("dataset", options)
        self.assertEqual(options["series"][0]["encode"]["x"], "${cat}")


class ModelValidationTests(TestCase):
    """What clean() refuses."""

    def test_several_statements_are_rejected(self):
        with self.assertRaises(ValidationError):
            Query(slug="q", title="Q", sql="SELECT 1; DROP TABLE x").clean()

    def test_a_semicolon_in_a_string_shall_not_be_mistaken_for_a_statement(self):
        Query(slug="q", title="Q", sql="SELECT string_agg(a, ';') FROM t").clean()

    def test_a_single_trailing_semicolon_is_allowed(self):
        Query(slug="q", title="Q", sql="SELECT 1;").clean()

    def test_a_placeholder_without_a_default_is_rejected(self):
        """The signature is probed by running the query alone, so it must be runnable."""
        with self.assertRaises(ValidationError):
            Query(slug="q", title="Q", sql="SELECT ${t}").clean()

    def test_a_chart_may_not_declare_a_dataset(self):
        with self.assertRaises(ValidationError):
            Chart(slug="c", title="C", options={"dataset": [{"source": []}]}).clean()

    def test_a_span_wider_than_the_grid_is_rejected(self):
        query = Query.objects.create(slug="q", title="Q", sql="SELECT 1")
        chart = Chart.objects.create(slug="c", title="C")

        with self.assertRaises(ValidationError):
            Panel(slug="p", query=query, chart=chart, span=99).clean()

    def test_a_binding_naming_an_unknown_column_is_rejected(self):
        query = Query.objects.create(
            slug="q", title="Q", sql="SELECT 1",
            signature=[{"name": "tenant", "type_oid": 25, "kind": "category"}],
        )
        chart = Chart.objects.create(slug="c", title="C")
        panel = Panel(slug="p", query=query, chart=chart, bindings={"x": "nope"})

        with self.assertRaises(ValidationError):
            panel.clean()

    def test_available_columns_follow_the_shaping_pipe(self):
        query = Query.objects.create(
            slug="q", title="Q", sql="SELECT 1",
            signature=[
                {"name": "team", "type_oid": 25, "kind": "category"},
                {"name": "effort", "type_oid": 23, "kind": "number"},
                {"name": "state", "type_oid": 25, "kind": "category"},
            ],
        )
        chart = Chart.objects.create(slug="c", title="C")
        panel = Panel(slug="p", query=query, chart=chart, transforms=[{
            "type": transforms.AGGREGATE,
            "config": {
                "resultDimensions": [{"from": "team"}, {"from": "effort", "method": "sum"}],
                "groupBy": "team",
            },
        }])

        self.assertEqual([column["name"] for column in panel.available_columns],
                         ["team", "effort"])

    def test_a_signature_is_not_cleared_when_the_probe_fails(self):
        """A moment's trouble reaching the analytics role must not empty every dropdown."""
        signature = [{"name": "tenant", "type_oid": 25, "kind": "category"}]
        query = Query.objects.create(slug="q", title="Q", sql="SELECT 1",
                                     signature=signature)

        query.save()

        self.assertEqual(Query.objects.get(pk=query.pk).signature, signature)

    def test_columns_do_not_reprobe_once_a_signature_is_known(self):
        """The heal is for a query that never had one, not a cache to refresh."""
        query = Query.objects.create(
            slug="q", title="Q", sql="SELECT 1",
            signature=[{"name": "tenant", "type_oid": 25, "kind": "category"}],
        )
        calls = []
        query.probe = lambda: calls.append(1) or False

        self.assertEqual(query.columns, ["tenant"])
        self.assertEqual(calls, [])

    def test_a_panel_falls_back_to_the_querys_title(self):
        query = Query.objects.create(slug="q", title="From the query", sql="SELECT 1")
        chart = Chart.objects.create(slug="c", title="C")

        self.assertEqual(Panel(slug="p", query=query, chart=chart).heading,
                         "From the query")


class ExampleTests(TestCase):
    """The examples a fresh system starts with; see analytics/examples.py."""

    def test_the_examples_are_created(self):
        # post_migrate already ran for the test database, so they are in place.
        for example in EXAMPLE_QUERIES:
            self.assertTrue(Query.objects.filter(slug=example["slug"]).exists())
        for example in EXAMPLE_CHARTS:
            self.assertTrue(Chart.objects.filter(slug=example["slug"]).exists())
        for example in EXAMPLE_PANELS:
            self.assertTrue(Panel.objects.filter(slug=example["slug"]).exists())
        self.assertTrue(Dashboard.objects.filter(slug="home").exists())

    def test_running_again_shall_not_undo_an_edit(self):
        """A deployment must not overwrite what an administrator changed."""
        slug = EXAMPLE_PANELS[0]["slug"]
        Panel.objects.filter(slug=slug).update(title="Renamed by hand")

        create_examples()

        self.assertEqual(Panel.objects.get(slug=slug).title, "Renamed by hand")

    def test_a_deleted_example_comes_back_on_the_next_migrate(self):
        """Documented rather than desirable: get_or_create restores what is missing.

        Deleting an example is therefore not permanent the way deleting an example
        tenant is. An administrator who wants one gone for good empties its query or
        takes its panel off the dashboard, either of which survives.
        """
        slug = EXAMPLE_PANELS[0]["slug"]
        Panel.objects.filter(slug=slug).delete()

        create_examples()

        self.assertTrue(Panel.objects.filter(slug=slug).exists())

    def test_one_query_is_used_by_more_than_one_panel(self):
        """The reuse the split exists for has to be visible in what ships."""
        reused = Query.objects.get(slug="example-issues-by-state")

        self.assertGreater(reused.panels.count(), 1)

    def test_every_example_binds_every_slot_its_chart_declares(self):
        for example in EXAMPLE_PANELS:
            panel = Panel.objects.select_related("chart").get(slug=example["slug"])
            for slot in panel.chart.slots:
                self.assertIn(slot, panel.bindings, f"{panel.slug}: ${{{slot}}} unbound")

    def test_every_example_chart_shall_be_valid(self):
        """clean() is what the admin runs; an example must pass it."""
        for example in EXAMPLE_CHARTS:
            Chart.objects.get(slug=example["slug"]).full_clean()

    def test_every_example_shall_be_a_usable_option_object(self):
        """An example is the first thing anyone sees, so none may render empty.

        Whether a binding names a column the query actually returns needs the database,
        so tests/test_panels.py runs each example for real; this only checks the shape.
        """
        for example in EXAMPLE_PANELS:
            panel = Panel.objects.select_related("chart").get(slug=example["slug"])
            option = build(panel, ["tenant_id", "state", "issues"], [("a", "open", 1)])

            self.assertTrue(option["dataset"])
            self.assertTrue(option.get("series"), f"{panel.slug}: no series")
            for entry in option["series"]:
                self.assertNotIn("${", str(entry.get("encode")),
                                 f"{panel.slug}: a slot was left unbound")


class SharedResultTests(TestCase):
    """One query defined once has to cost one execution, not one per panel."""

    def setUp(self):
        caches[RESULT_CACHE].clear()

    def test_a_second_panel_reuses_the_first_result(self):
        calls = []

        def fake_run(sql_text, values):
            calls.append((sql_text, values))
            return ["a"], [(1,)]

        with mock.patch("analytics.query.run", side_effect=fake_run):
            first = run_shared("SELECT 1", {"t": "x"})
            second = run_shared("SELECT 1", {"t": "x"})

        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, "the query ran once per panel")

    def test_different_values_are_not_shared(self):
        """Two panels pointing one query at different tenants are two questions."""
        calls = []
        with mock.patch("analytics.query.run",
                        side_effect=lambda s, v: calls.append(v) or (["a"], [(1,)])):
            run_shared("SELECT 1", {"t": "a"})
            run_shared("SELECT 1", {"t": "b"})

        self.assertEqual(len(calls), 2)

    def test_editing_the_query_takes_effect_at_once(self):
        """The statement is part of the key, so no edit waits for an entry to expire."""
        calls = []
        with mock.patch("analytics.query.run",
                        side_effect=lambda s, v: calls.append(s) or (["a"], [(1,)])):
            run_shared("SELECT 1", {})
            run_shared("SELECT 2", {})

        self.assertEqual(calls, ["SELECT 1", "SELECT 2"])


class DashboardLayoutTests(TestCase):
    """The grid, which has to survive a narrow screen."""

    def test_the_component_names_the_spans_its_style_block_needs(self):
        dashboard = Dashboard.objects.create(slug="d", title="D", columns=12)
        query = Query.objects.create(slug="q", title="Q", sql="SELECT 1")
        chart = Chart.objects.create(slug="c", title="C")
        for index, span in enumerate((6, 6, 4)):
            Panel.objects.create(slug=f"p{index}", dashboard=dashboard, query=query,
                                 chart=chart, span=span, order=index)

        context = DashboardComponent(request=None).get_context_data(dashboard="d")

        self.assertEqual(context["spans"], [4, 6])
        self.assertEqual(context["grid_id"], "gf-dashboard-d")

    def test_the_grid_collapses_below_the_breakpoint(self):
        """A span against a single column would ask for implicit columns and overflow."""
        dashboard = Dashboard.objects.create(slug="d", title="D", columns=12)
        query = Query.objects.create(slug="q", title="Q", sql="SELECT 1")
        chart = Chart.objects.create(slug="c", title="C")
        Panel.objects.create(slug="p", dashboard=dashboard, query=query, chart=chart,
                             span=6)

        rendered = render_to_string(
            "analytics/dashboard.html",
            DashboardComponent(request=None).get_context_data(dashboard="d"),
        )

        # Full width first, spans only inside the media query.
        self.assertIn("grid-column: 1 / -1", rendered)
        span_rule = rendered.index("gf-span-6 { grid-column: span 6")
        self.assertLess(rendered.index("@media (min-width: 1024px)"), span_rule)


class DashboardInlineTests(TestCase):
    """Adding a panel from its dashboard has to produce a usable row."""

    def test_the_inline_offers_the_slug(self):
        """A field the inline leaves out is excluded from validation, so the panel would
        be saved with an empty slug and the second one would collide with the first."""
        self.assertIn("slug", PanelInline.fields)


class PrettyJsonTests(TestCase):
    """The option object is written by pasting one in, so the field has to stay readable.

    jsonb keeps the value and not its text, so indentation cannot be stored; the form
    indents on the way out instead. Django renders a JSON form field with
    json.dumps(value, cls=self.encoder), which is where this encoder gets its say.
    """

    def test_the_encoder_indents(self):
        rendered = PrettyJSONEncoder().encode({"series": [{"type": "bar"}]})

        self.assertIn("\n", rendered)
        self.assertIn('\n  "series"', rendered)

    def test_the_encoder_keeps_the_authors_key_order(self):
        """Sorting would reshuffle a pasted option object on every save."""
        rendered = PrettyJSONEncoder().encode({"yAxis": 1, "xAxis": 2})

        self.assertLess(rendered.index("yAxis"), rendered.index("xAxis"))

    def test_the_json_fields_use_it(self):
        for model, names in (
            (Query, ("parameter_defaults", "checks")),
            (Chart, ("options", "transforms")),
            (Panel, ("parameters", "transforms", "bindings")),
        ):
            for name in names:
                self.assertIs(model._meta.get_field(name).encoder, PrettyJSONEncoder)

    def test_the_form_renders_the_stored_value_indented(self):
        """What the admin actually puts in the textarea."""
        from django.forms.models import modelform_factory

        form = modelform_factory(Chart, fields=["options"])(
            instance=Chart(slug="c", title="C", options={"series": [{"type": "bar"}]})
        )
        value = form["options"].value()

        self.assertIn("\n", value)
