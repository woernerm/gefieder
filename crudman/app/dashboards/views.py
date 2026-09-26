"""The dashboards: a list of them, each one's page, and its panels one by one.

A page is drawn first with its filters and an empty box per panel; each box then asks for
its panel (htmx), and asks again when a filter it reads changes. So the slowest query holds
up its own panel and nothing else.

Ordinary Django views rather than admin pages, open from the viewer rank up like the
documentation, and in Unfold's layout like it.
"""
from django.contrib import admin
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.views.generic import TemplateView, View

from docs.access import ViewerRequiredMixin

from .service import Unavailable, ask, ask_json


class DashboardsView(ViewerRequiredMixin, TemplateView):
    """What every dashboards page shares: Unfold's chrome and the list in the sidebar."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(admin.site.each_context(self.request))
        context["templates"] = {"navigation": "dashboards/navigation.html"}
        try:
            _, listing = ask_json(self.request)
        except Unavailable as error:
            listing = {"dashboards": [], "problems": {"": str(error)}}
        context["listing"] = listing
        return context


class IndexView(DashboardsView):
    template_name = "dashboards/index.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "title": "Dashboards"}


class DashboardView(DashboardsView):
    template_name = "dashboards/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        name = self.kwargs["name"]
        try:
            status, board = ask_json(self.request, f"{name}/")
        except Unavailable as error:
            status, board = 503, {"detail": str(error)}
        if status == 404:
            raise Http404(board["detail"])
        return {**context, "name": name, "board": board, "title": board.get("title", name)}


class PanelView(ViewerRequiredMixin, View):
    """One panel, drawn: the payload the page's script turns into a chart."""

    def get(self, request, name, index):
        try:
            _, payload = ask_json(request, f"{name}/{index}/")
        except Unavailable as error:
            payload = {"kind": "error", "message": str(error)}
        return render(request, "dashboards/panel.html", {"payload": payload})


class DownloadView(ViewerRequiredMixin, View):
    """A panel's data as an Excel workbook, filtered as the page is."""

    def get(self, request, name, index):
        try:
            status, body = ask(request, f"{name}/{index}.xlsx")
        except Unavailable as error:
            return HttpResponse(str(error), status=503, content_type="text/plain")
        if status != 200:
            return HttpResponse(body, status=status, content_type="application/json")
        response = HttpResponse(
            body, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = f'attachment; filename="{name}-{index + 1}.xlsx"'
        return response
