"""The dashboards: a list of them, each one's page, and its panels one by one.

A page is drawn first with its filters and an empty box per panel; each box then asks for
its panel (htmx), and asks again when a filter it reads changes. So the slowest query holds
up its own panel and nothing else.

Ordinary Django views rather than admin pages, open from the viewer rank up like the
documentation, and in Unfold's layout like it.
"""
from django.contrib import admin
from django.http import Http404, HttpResponse
from django.utils.html import json_script
from django.views.generic import TemplateView, View

from docs.access import ViewerRequiredMixin

from .service import ask, ask_json


class DashboardsView(ViewerRequiredMixin, TemplateView):
    """What every dashboards page shares: Unfold's chrome and the list in the sidebar."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(admin.site.each_context(self.request))
        context["templates"] = {"navigation": "dashboards/navigation.html"}
        status, listing = ask_json(self.request)
        if status != 200:
            listing = {"dashboards": [], "problems": {"": listing["detail"]}}
        context["listing"] = listing
        return context


class IndexView(DashboardsView):
    template_name = "dashboards/index.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "title": "Dashboards"}


QUICK_RANGES = [
    ("now-5m", "Last 5 minutes"), ("now-15m", "Last 15 minutes"), ("now-1h", "Last hour"),
    ("now-6h", "Last 6 hours"), ("now-24h", "Last 24 hours"), ("now-7d", "Last 7 days"),
    ("now-30d", "Last 30 days"), ("now-90d", "Last 90 days"), ("now-1y", "Last year"),
]
"""The time picker's quick choices, written as the time range is: Grafana's way."""

REFRESH_INTERVALS = ["off", "10s", "30s", "1m", "5m", "15m", "1h"]
"""What the refresh picker offers. "off" is a value of its own rather than an empty one, which
the address bar would drop -- and a reload fall back to the dashboard's own interval."""


class DashboardView(DashboardsView):
    template_name = "dashboards/dashboard.html"
    extra_context = {"quick_ranges": QUICK_RANGES, "refresh_intervals": REFRESH_INTERVALS}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        name = self.kwargs["name"]
        status, board = ask_json(self.request, f"{name}/")
        if status == 404:
            raise Http404(board["detail"])
        return {**context, "name": name, "board": board, "title": board.get("title", name)}


class PanelView(ViewerRequiredMixin, View):
    """One panel, drawn: the payload the page's script turns into a chart."""

    def get(self, request, name, index):
        status, payload = ask_json(request, f"{name}/{index}/")
        if status != 200:
            payload = {"kind": "error", "message": payload["detail"]}
        # dashboards.js draws it once htmx has put it in the page.
        return HttpResponse(json_script(payload))


class DownloadView(ViewerRequiredMixin, View):
    """A panel's data as an Excel workbook, filtered as the page is."""

    def get(self, request, name, index):
        status, body = ask(request, f"{name}/{index}.xlsx")
        if status != 200:
            return HttpResponse(body, status=status, content_type="application/json")
        response = HttpResponse(
            body, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{name}-{index + 1}.xlsx"'
        return response
