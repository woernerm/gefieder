"""The model documentation pages.

Rendered from the JSON the SQLMesh image exported at build time (see
sqlmesh/docs_export.py), so the pages describe exactly the models that shipped with this
release and need neither the SQLMesh project files nor a query against the engine.

The pages are ordinary Django views rather than admin pages: the documentation is open
from the viewer rank upwards, and the admin requires staff. They still extend Unfold's
own layout, so the look is the admin's without being part of it.
"""

import json
import os
from functools import cache
from pathlib import Path

from django.contrib import admin
from django.http import Http404
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.generic import TemplateView
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import SqlLexer

from . import lineage
from .access import ViewerRequiredMixin

DOCS_PATH = Path(os.environ.get("SQLMESH_DOCS_PATH", "/crudman/docs.json"))
"""Where the exported documentation is baked into the image."""

SQL_FORMATTER = HtmlFormatter(nowrap=True)
"""Highlights into bare spans; the surrounding <pre> and its styling are the template's,
so the code block matches Unfold rather than carrying a theme of its own."""


@cache
def documentation() -> dict:
    """The exported documentation, read once per process.

    Returns:
        The layers and their models, or empty layers when the file is missing -- a
        checkout run without a build has no export, and an empty page is a better answer
        there than a broken admin.
    """
    if not DOCS_PATH.exists():
        return {"layers": []}
    return json.loads(DOCS_PATH.read_text())


@cache
def sql_styles() -> str:
    """The colours for the highlighted SQL.

    Only the rules for the token classes: Pygments emits a bare ``pre`` rule and a set of
    line-number rules whatever selector it is given, and the first would reach every
    other preformatted block on the page while the rest match nothing, the definitions
    being highlighted without line numbers.
    """
    rules = HtmlFormatter().get_style_defs(".highlight").splitlines()
    return "\n".join(rule for rule in rules if rule.startswith(".highlight"))


class DocsView(ViewerRequiredMixin, TemplateView):
    """Shared chrome for the documentation pages.

    Unfold's layout reads what it draws -- theme, colours, branding, the user menu --
    from the admin site's context, so it is merged in here. Its own sidebar navigation
    is replaced rather than extended: the admin's app list is staff-only and would be
    empty for a viewer, while the documentation's own table of contents is the same for
    everyone who may read the page at all.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(admin.site.each_context(self.request))
        context.update(
            {
                "templates": {"navigation": "docs/navigation.html"},
                "docs_layers": documentation()["layers"],
                "sql_styles": mark_safe(sql_styles()),
            }
        )
        return context


class IndexView(DocsView):
    """The documentation's front page: the lineage, then what each layer holds."""

    template_name = "docs/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = "Model documentation"
        models = [
            model for layer in context["docs_layers"] for model in layer["models"]
        ]
        chart = lineage.chart(
            models,
            lambda model: (
                reverse("docs:layer", args=[model["layer"]]) + f"#{model['name']}"
            ),
        )
        # Through json_script in the template rather than mark_safe: it is data for a
        # script to read, and that tag escapes it for exactly that place.
        context["lineage"] = chart
        return context


class LayerView(DocsView):
    """One medallion layer, with each of its models in full."""

    template_name = "docs/layer.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        name = self.kwargs["layer"]
        layer = next(
            (entry for entry in context["docs_layers"] if entry["name"] == name), None
        )
        if layer is None:
            raise Http404(f"No documentation for layer {name!r}")

        context["layer"] = {
            **layer,
            "models": [
                # Pygments emits the markup it was asked for; the input is this
                # repository's own models, not anything a visitor supplies.
                {
                    **model,
                    "sql_html": mark_safe(
                        highlight(model["sql"], SqlLexer(), SQL_FORMATTER)
                    ),
                }
                for model in layer["models"]
            ],
        }
        context["title"] = f"{name.capitalize()} models"
        return context
