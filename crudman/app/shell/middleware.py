"""The bar below every page: one top-level document, with the app in a frame inside it.

Sec-Fetch-Dest tells the proxy a page for the address bar ("document") from one for a
frame ("iframe"). It sends the former here, whichever app the path belongs to, and this
answers with the shell, whose frame asks for the same path again and reaches the app. So
bookmarks and reloads land inside the bar, and no app has to know it is framed.
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render
from django.urls import reverse

from notebooks.views import FORWARDED
from system.models import Approval

from .models import lately, seen
from .stages import HOME, stages_for


def is_page(request):
    """Whether the browser asked for this as a page of its own, through the proxy.

    Through the proxy, as the proxy's identity check for Grafana copies the browser's
    headers. The admin's related-object popups talk to the window that opened them, which a
    frame cannot.
    """
    return (
        request.method == "GET"
        and request.headers.get("Sec-Fetch-Dest") == "document"
        and FORWARDED in request.META
        and "_popup" not in request.GET
    )


def shell(get_response):
    """Answer a page request with the shell, and send anonymous visitors to the sign-in."""

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
                # The bar is where a stakeholder answers, because it is the one thing on
                # every page: they were sent a link to a dashboard, not to a form.
                "approval": Approval.owed_by(user),
            })
        # The admin panel's own pages sign a visitor in themselves; another service's
        # address has no route here to do it, so the redirect happens here instead.
        if request.path.startswith(f"/{settings.CRUDMAN_PATH}/"):
            return get_response(request)
        return redirect_to_login(request.get_full_path(), reverse("login"))

    return middleware
