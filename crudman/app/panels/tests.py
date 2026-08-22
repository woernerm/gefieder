"""Unit tests for the panel model and its ECharts mapping.

The guards that matter -- read-only execution and the analytics role's own grants -- need
a real database and are covered by tests/test_panels.py in the integration suite.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from .charts import ColumnMissing, build, merge
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

    def test_panel_options_are_merged_over_the_generated_ones(self):
        panel = Panel(slug="p", title="P", options={"yAxis": {"type": "log"}})
        option = build(panel, ["a", "b"], [("x", 1)])
        self.assertEqual(option["yAxis"]["type"], "log")
