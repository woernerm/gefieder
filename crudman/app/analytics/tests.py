"""Unit tests for the panel model and its ECharts mapping.

The guards that matter -- read-only execution and the analytics role's own grants -- need
a real database and are covered by tests/test_panels.py in the integration suite.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from .charts import build
from .encoders import PrettyJSONEncoder
from .examples import EXAMPLE_PANELS, create_example_panels
from .models import Panel


class PanelModelTests(TestCase):
    """What clean() rejects. Which columns a chart uses is the option object's business
    now, so there is nothing here about axes."""

    def test_several_statements_are_rejected(self):
        panel = Panel(slug="p", title="P", sql="SELECT 1; DROP TABLE x")
        with self.assertRaises(ValidationError):
            panel.clean()

    def test_a_single_trailing_semicolon_is_allowed(self):
        Panel(slug="p", title="P", sql="SELECT 1;").clean()


class DatasetTests(TestCase):
    """What build() injects; the option object itself is ECharts' business, not ours."""

    def test_the_query_result_becomes_the_dataset(self):
        panel = Panel(slug="p", title="P", options={"series": [{"type": "bar"}]})
        option = build(panel, ["tenant", "open"], [("a", 1), ("b", 2)])

        self.assertEqual(option["dataset"]["dimensions"], ["tenant", "open"])
        self.assertEqual(option["dataset"]["source"], [["a", 1], ["b", 2]])

    def test_the_stored_options_are_left_alone(self):
        """The option object is the chart; nothing here may second-guess it."""
        options = {"xAxis": {"type": "category"}, "series": [{"type": "line"}]}
        option = build(Panel(slug="p", title="P", options=options), ["a"], [(1,)])

        self.assertEqual(option["xAxis"], {"type": "category"})
        self.assertEqual(option["series"], [{"type": "line"}])

    def test_a_panels_own_dataset_is_kept_after_the_query_result(self):
        """A transform naming no source reads index 0, so the rows have to come first."""
        panel = Panel(
            slug="p", title="P",
            options={"dataset": [{"transform": {"type": "sort"}}]},
        )
        option = build(panel, ["a"], [(1,)])

        self.assertEqual(option["dataset"][0]["source"], [[1]])
        self.assertEqual(option["dataset"][1], {"transform": {"type": "sort"}})

    def test_a_single_declared_dataset_becomes_a_list(self):
        panel = Panel(slug="p", title="P", options={"dataset": {"source": [[9]]}})
        option = build(panel, ["a"], [(1,)])

        self.assertEqual([entry.get("source") for entry in option["dataset"]],
                         [[[1]], [[9]]])

    def test_values_json_cannot_carry_are_converted(self):
        from datetime import date
        from decimal import Decimal

        option = build(
            Panel(slug="p", title="P"), ["d", "n"], [(date(2026, 1, 2), Decimal("1.5"))]
        )

        self.assertEqual(option["dataset"]["source"], [["2026-01-02", 1.5]])

    def test_building_shall_not_mutate_the_stored_options(self):
        """build() runs on a model instance the request still holds."""
        options = {"series": [{"type": "bar"}]}
        panel = Panel(slug="p", title="P", options=options)
        build(panel, ["a"], [(1,)])

        self.assertNotIn("dataset", options)


class ExamplePanelTests(TestCase):
    """The examples a fresh system starts with; see panels/examples.py."""

    def test_the_examples_are_created(self):
        # post_migrate already ran for the test database, so they are in place.
        for example in EXAMPLE_PANELS:
            self.assertTrue(Panel.objects.filter(slug=example["slug"]).exists())

    def test_running_again_shall_not_undo_an_edit(self):
        """A deployment must not overwrite what an administrator changed."""
        slug = EXAMPLE_PANELS[0]["slug"]
        Panel.objects.filter(slug=slug).update(title="Renamed by hand")

        create_example_panels()

        self.assertEqual(Panel.objects.get(slug=slug).title, "Renamed by hand")

    def test_a_deleted_example_comes_back_on_the_next_migrate(self):
        """Documented rather than desirable: get_or_create restores what is missing.

        Deleting an example is therefore not permanent the way deleting an example
        tenant is. An administrator who wants one gone for good empties its query or
        unticks the dashboard flag, either of which survives.
        """
        slug = EXAMPLE_PANELS[0]["slug"]
        Panel.objects.filter(slug=slug).delete()

        create_example_panels()

        self.assertTrue(Panel.objects.filter(slug=slug).exists())

    def test_every_example_shall_be_valid(self):
        """clean() is what the admin runs; an example must pass it."""
        for example in EXAMPLE_PANELS:
            Panel.objects.get(slug=example["slug"]).full_clean()

    def test_every_example_shall_be_a_usable_option_object(self):
        """An example is the first thing anyone sees, so none may render empty.

        Whether an encode names a column the query actually returns needs the database,
        so tests/test_panels.py runs each example for real; this only checks the shape.
        """
        for example in EXAMPLE_PANELS:
            panel = Panel.objects.get(slug=example["slug"])
            option = build(panel, ["tenant_id", "open_issues"], [("a", 1)])

            self.assertIn("dataset", option)
            if not panel.options.get("table"):
                self.assertTrue(option.get("series"), f"{panel.slug}: no series")


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
        for name in ("options", "parameters"):
            self.assertIs(Panel._meta.get_field(name).encoder, PrettyJSONEncoder)

    def test_the_form_renders_the_stored_value_indented(self):
        """What the admin actually puts in the textarea."""
        from django.forms.models import modelform_factory

        form = modelform_factory(Panel, fields=["options"])(
            instance=Panel(slug="p", title="P", options={"series": [{"type": "bar"}]})
        )
        value = form["options"].value()

        self.assertIn("\n", value)
