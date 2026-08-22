"""Unit tests for the panel model and its ECharts mapping.

The guards that matter -- read-only execution and the analytics role's own grants -- need
a real database and are covered by tests/test_panels.py in the integration suite.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from .charts import ColumnMissing, build, merge
from .examples import EXAMPLE_PANELS, create_example_panels
from .models import Panel


class PanelModelTests(TestCase):
    def test_category_defaults_to_the_first_column(self):
        panel = Panel(slug="p", title="P")
        self.assertEqual(panel.category_column(["a", "b", "c"]), "a")

    def test_values_default_to_every_other_column(self):
        panel = Panel(slug="p", title="P")
        self.assertEqual(panel.value_columns(["a", "b", "c"]), ["b", "c"])

    def test_explicit_fields_win(self):
        panel = Panel(slug="p", title="P", x_field="b", y_fields="a, c")
        self.assertEqual(panel.category_column(["a", "b", "c"]), "b")
        self.assertEqual(panel.value_columns(["a", "b", "c"]), ["a", "c"])

    def test_several_statements_are_rejected(self):
        panel = Panel(slug="p", title="P", sql="SELECT 1; DROP TABLE x")
        with self.assertRaises(ValidationError):
            panel.clean()

    def test_a_single_trailing_semicolon_is_allowed(self):
        Panel(slug="p", title="P", sql="SELECT 1;").clean()


class ChartTests(TestCase):
    def test_merge_is_recursive_and_the_override_wins(self):
        self.assertEqual(
            merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}}),
            {"a": {"x": 1, "y": 3}, "b": 1},
        )

    def test_one_series_per_value_column(self):
        panel = Panel(slug="p", title="P", chart_type=Panel.BAR)
        option = build(panel, ["tenant", "open", "closed"], [("a", 1, 2), ("b", 3, 4)])
        self.assertEqual(option["xAxis"]["data"], ["a", "b"])
        self.assertEqual([s["name"] for s in option["series"]], ["open", "closed"])
        self.assertEqual(option["series"][0]["data"], [1, 3])

    def test_pie_uses_name_value_pairs(self):
        panel = Panel(slug="p", title="P", chart_type=Panel.PIE)
        option = build(panel, ["tenant", "open"], [("a", 1)])
        self.assertEqual(option["series"][0]["data"], [{"name": "a", "value": 1}])

    def test_a_column_the_query_does_not_return_is_reported(self):
        """A typo in the panel names a column that is not there; say which."""
        panel = Panel(slug="p", title="P", x_field="typo")
        with self.assertRaises(ColumnMissing):
            build(panel, ["tenant", "open"], [("a", 1)])

        panel = Panel(slug="p", title="P", y_fields="missing")
        with self.assertRaises(ColumnMissing):
            build(panel, ["tenant", "open"], [("a", 1)])

    def test_an_override_shall_refine_a_series_not_replace_it(self):
        """The series carries the query's data; an override naming one setting kept it.

        Replacing the list outright left {"radius": ...} alone in series, with no type
        and no data, which ECharts draws as an empty chart.
        """
        panel = Panel(
            slug="p", title="P", chart_type=Panel.PIE,
            options={"series": [{"radius": ["45%", "70%"]}]},
        )
        series = build(panel, ["tenant", "effort"], [("a", 1)])["series"][0]

        self.assertEqual(series["type"], "pie")
        self.assertEqual(series["data"], [{"name": "a", "value": 1}])
        self.assertEqual(series["radius"], ["45%", "70%"])

    def test_merge_keeps_the_entries_only_one_side_has(self):
        self.assertEqual(
            merge({"s": [{"a": 1}]}, {"s": [{"b": 2}, {"c": 3}]}),
            {"s": [{"a": 1, "b": 2}, {"c": 3}]},
        )

    def test_panel_options_are_merged_over_the_generated_ones(self):
        panel = Panel(slug="p", title="P", options={"yAxis": {"type": "log"}})
        option = build(panel, ["a", "b"], [("x", 1)])
        self.assertEqual(option["yAxis"]["type"], "log")


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

    def test_every_example_with_options_shall_still_carry_its_data(self):
        """An example is the first thing anyone sees, so none may render empty."""
        for example in EXAMPLE_PANELS:
            panel = Panel.objects.get(slug=example["slug"])
            if panel.chart_type == Panel.TABLE:
                continue
            option = build(panel, ["label", "value"], [("a", 1), ("b", 2)])
            for series in option["series"]:
                self.assertIn("type", series, f"{panel.slug}: series lost its type")
                self.assertTrue(series.get("data"), f"{panel.slug}: series lost its data")
