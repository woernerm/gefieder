"""The bar below every page: one top-level document, with the app in a frame inside it.

A browser asks for a page in two ways the proxy can tell apart: Sec-Fetch-Dest says
"document" for what goes in the address bar and "iframe" for what goes in a frame. The
proxy sends the former here whichever app the path belongs to, and this answers it with
the shell -- whose frame then asks for the same path again, and that request reaches the
app. So the address never changes: a bookmark, a link in a mail and a reload all land
inside the bar, and no app has to know it is framed.
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render
from django.urls import reverse

from notebooks.views import FORWARDED

from .models import lately, seen
from .stages import HOME, stages_for


def is_page(request):
    """Whether the browser asked for this as a page of its own, through the proxy.

    Through the proxy, because the proxy asks here about Grafana's visitors with their
    request's headers copied, and that answer must stay an answer. The admin's related-
    object popups are pages of their own too, but talk to the window that opened them,
    which a frame cannot.
    """
    return (
        request.method == "GET"
        and request.headers.get("Sec-Fetch-Dest") == "document"
        and FORWARDED in request.META
        and "_popup" not in request.GET
    )


def shell(get_response):
    def middleware(request):
        user = request.user
        if user.is_authenticated:
            seen(user)
        if not is_page(request):
            return get_response(request)
        if user.is_authenticated:
            return render(request, "shell/shell.html", {
                **admin.site.each_context(request),
                "home": HOME.url,
                "grafana": f"/{settings.GRAFANA_PATH}/",
                "stages": stages_for(request),
                "online": len(lately()["online"]),
            })
        # The admin panel's own pages sign a visitor in themselves; another service's
        # address has no route here to do it, so the redirect happens here instead.
        if request.path.startswith(f"/{settings.CRUDMAN_PATH}/"):
            return get_response(request)
        return redirect_to_login(request.get_full_path(), reverse("login"))

    return middleware
