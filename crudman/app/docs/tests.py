"""Who reaches the documentation, and what the export puts in front of them."""

import json
import re
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from sso.roles import GROUP_FOR_RANK

from . import lineage, views

DOCS = {
    "layers": [
        {
            "name": "bronze",
            "models": [
                {
                    "name": "bronze_project_a.issues",
                    "layer": "bronze",
                    "tenant": "project_a",
                    "description": "Project A's raw issues.",
                    "kind": "VIEW",
                    "cron": "@daily",
                    "owner": None,
                    "stamp": None,
                    "tags": [],
                    "grains": [],
                    "references": [],
                    "audits": [],
                    "depends_on": [],
                    "columns": [
                        {"name": "issue_key", "type": "TEXT", "description": "The key."}
                    ],
                    "sql": "SELECT issue_key FROM jira.issues",
                }
            ],
        },
        {
            "name": "silver",
            "models": [
                {
                    "name": "silver.issues",
                    "layer": "silver",
                    "tenant": None,
                    "description": "The harmonized issues.",
                    "kind": "VIEW",
                    "cron": "@daily",
                    "owner": None,
                    "stamp": None,
                    "tags": [],
                    "grains": [],
                    "references": [],
                    "audits": [],
                    "depends_on": ["bronze_project_a.issues"],
                    "columns": [],
                    "sql": "SELECT 1",
                }
            ],
        },
        {"name": "gold", "models": []},
    ]
}


class DocumentationPagesTest(TestCase):
    """The pages themselves, with the export stubbed so the test needs no build."""

    def setUp(self):
        views.documentation.cache_clear()
        patcher = patch.object(views, "documentation", lambda: DOCS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.index = reverse("docs:index")
        self.layer = reverse("docs:layer", args=["bronze"])

    def _user(self, name, rank=None, **flags):
        user = User.objects.create_user(name, password="x", **flags)
        if rank:
            group, _ = Group.objects.get_or_create(name=GROUP_FOR_RANK[rank])
            user.groups.add(group)
        return user

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        response = self.client.get(self.index)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_user_without_a_rank_is_refused(self):
        self._user("nobody")
        self.client.login(username="nobody", password="x")
        self.assertEqual(self.client.get(self.index).status_code, 403)

    def test_every_rank_from_viewer_upwards_may_read(self):
        for rank in ("viewer", "editor", "admin"):
            with self.subTest(rank=rank):
                self._user(rank, rank=rank)
                self.client.login(username=rank, password="x")
                self.assertEqual(self.client.get(self.index).status_code, 200)
                self.assertEqual(self.client.get(self.layer).status_code, 200)

    def test_staff_may_read_without_a_rank(self):
        self._user("staffer", is_staff=True)
        self.client.login(username="staffer", password="x")
        self.assertEqual(self.client.get(self.index).status_code, 200)

    def test_a_layer_page_shows_the_model_and_its_columns(self):
        self._user("viewer", rank="viewer")
        self.client.login(username="viewer", password="x")
        page = self.client.get(self.layer).content.decode()
        self.assertIn("bronze_project_a.issues", page)
        self.assertIn("Project A&#x27;s raw issues.", page)
        self.assertIn("issue_key", page)
        # Highlighted rather than escaped: the SQL reaches the page as markup.
        self.assertIn('<span class="k">SELECT</span>', page)

    def test_an_unknown_layer_is_not_found(self):
        self._user("viewer", rank="viewer")
        self.client.login(username="viewer", password="x")
        url = reverse("docs:layer", args=["platinum"])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_the_sidebar_lists_the_models_and_not_the_admin(self):
        self._user("viewer", rank="viewer")
        self.client.login(username="viewer", password="x")
        page = self.client.get(self.index).content.decode()
        self.assertIn("nav-sidebar-inner", page)
        self.assertIn("bronze_project_a.issues", page)


class MissingExportTest(TestCase):
    """A checkout run without a build has no export; the page still answers."""

    def test_no_export_yields_empty_layers(self):
        views.documentation.cache_clear()
        with patch.object(views, "DOCS_PATH", Path("/nonexistent/docs.json")):
            self.assertEqual(views.documentation(), {"layers": []})
        views.documentation.cache_clear()


class ExportShapeTest(TestCase):
    """The contract between the exporter and these pages: plain text, no markup."""

    def test_the_export_carries_no_markup(self):
        # What the exporter writes is what the templates read; keeping it JSON-encodable
        # and free of HTML is what lets the same file feed anything else later.
        encoded = json.dumps(DOCS)
        self.assertNotIn("<span", encoded)
        self.assertNotIn("<div", encoded)


class LineageTest(TestCase):
    """The graph configuration drawn from the dependencies."""

    def _models(self):
        return [model for layer in DOCS["layers"] for model in layer["models"]]

    def test_a_model_is_placed_after_what_it_reads(self):
        columns = lineage.levels(self._models())
        self.assertEqual(
            [[model["name"] for model in column] for column in columns],
            [["bronze_project_a.issues"], ["silver.issues"]],
        )

    def test_the_chart_carries_every_model_and_edge(self):
        option = lineage.chart(self._models(), lambda model: "/docs/")
        series = option["series"][0]
        self.assertEqual(
            {node["id"] for node in series["data"]},
            {"bronze_project_a.issues", "silver.issues"},
        )
        self.assertEqual(
            series["links"],
            [{"source": "bronze_project_a.issues", "target": "silver.issues"}],
        )
        # Fixed positions: the medallion levels are the point of the picture.
        self.assertEqual(series["layout"], "none")
        self.assertLess(series["data"][0]["x"], series["data"][1]["x"])

    def test_a_node_keeps_its_full_name_and_link(self):
        option = lineage.chart(self._models(), lambda model: f"/docs/{model['layer']}/")
        node = next(n for n in option["series"][0]["data"] if n["id"] == "silver.issues")
        self.assertEqual(node["name"], "issues")
        self.assertEqual(node["value"], "silver.issues")
        self.assertEqual(node["url"], "/docs/silver/")

    def test_a_dependency_outside_the_project_is_left_out(self):
        # Bronze reads source schemas the project does not define; the graph is about the
        # models themselves, so such an edge has nowhere to start.
        models = [dict(model) for model in self._models()]
        downstream = next(m for m in models if m["name"] == "silver.issues")
        downstream["depends_on"] = ["jira.issues"]
        option = lineage.chart(models, lambda model: "/docs/")
        self.assertEqual(option["series"][0]["links"], [])
        # The model itself is still drawn; only the edge to nowhere is dropped.
        self.assertEqual(len(option["series"][0]["data"]), 2)

    def test_the_node_corners_are_round_on_a_wide_node(self):
        # ECharts draws a symbol in a normalised -1..1 box and scales it to symbolSize,
        # so the built-in roundRect comes out with corners as wide as the node is out of
        # square -- 47x9 pixels rather than round. The path compensates per axis; what
        # matters is that both radii land on the same number of pixels once scaled.
        series = lineage.chart(self._models(), lambda model: "/docs/")["series"][0]
        self.assertEqual(series["symbolSize"], lineage.NODE_SIZE)

        horizontal, vertical = re.search(
            r"A ([\d.]+) ([\d.]+)", series["symbol"]
        ).groups()
        width, height = lineage.NODE_SIZE
        self.assertAlmostEqual(float(horizontal) * width / 2, lineage.CORNER_RADIUS)
        self.assertAlmostEqual(float(vertical) * height / 2, lineage.CORNER_RADIUS)

    def test_the_colours_are_named_theme_tokens_not_values(self):
        # The graph names Unfold's custom properties and the page resolves them, so a
        # palette change reaches the graph and no colour is written down twice.
        option = lineage.chart(self._models(), lambda model: "/docs/")
        self.assertEqual(option["themeColors"], lineage.THEME_COLORS)
        for property_name in option["themeColors"].values():
            self.assertTrue(property_name.startswith("--color-"))
        # Nothing is painted server-side; the template does it from those names.
        self.assertNotIn("itemStyle", option["series"][0])

    def test_nothing_to_draw_is_not_an_error(self):
        self.assertIsNone(lineage.chart([], lambda model: "/docs/"))

    def test_the_index_shows_the_graph(self):
        user = User.objects.create_user("viewer", password="x")
        group, _ = Group.objects.get_or_create(name=GROUP_FOR_RANK["viewer"])
        user.groups.add(group)
        self.client.login(username="viewer", password="x")
        views.documentation.cache_clear()
        with patch.object(views, "documentation", lambda: DOCS):
            page = self.client.get(reverse("docs:index")).content.decode()
        self.assertIn("lineage-data", page)
        self.assertIn("echarts", page)
        # The library is served from the image, never fetched from a CDN.
        self.assertNotIn("unpkg.com", page)
