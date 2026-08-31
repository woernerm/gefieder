"""The admin's login and logout URLs, which route both through the identity provider."""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import logout as end_session
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .avatars import PICTURE_MAX_AGE, stored_picture

LOCAL_LOGIN_PARAM = "local"
"""Query parameter that reaches the local login form while single sign-on is on.

The way back in for the superuser when the provider is unreachable, so it is named in the
README. Grafana's equivalent is /login?disableAutoLogin.
"""


def login(request):
    """Redirect to the provider, or show the local form.

    A visitor who still has a session with the provider is signed in without seeing a
    page at all, which is the point of doing this instead of offering a button.

    Args:
        request: The HTTP request. The local form posts back here without the query
            string, so a POST counts as local or the escape hatch would be unusable.

    Returns:
        The admin's login page, or a redirect to the provider.
    """
    local = request.method == "POST" or LOCAL_LOGIN_PARAM in request.GET
    if not settings.OIDC_ENABLED or local:
        return admin.site.login(request)

    params = {"process": "login"}
    if next_url := request.GET.get("next"):
        params["next"] = next_url

    target = reverse(
        "openid_connect_login", kwargs={"provider_id": settings.OIDC_PROVIDER_ID}
    )
    return redirect(f"{target}?{urlencode(params)}")


def logout(request):
    """End the session here, then send the browser on to end the provider's.

    Without the second half the provider still holds its session, and ``login`` hands it
    straight back on the next page view.

    Args:
        request: The HTTP request. Anything but a POST is left to the admin, which answers
            a GET with "method not allowed" so a link cannot sign someone out.

    Returns:
        A redirect to the provider's logout URL, or the admin's own logout response.
    """
    signs_out_at_provider = settings.OIDC_ENABLED and settings.OIDC_LOGOUT_URL
    if request.method != "POST" or not signs_out_at_provider:
        return admin.site.logout(request)

    end_session(request)
    return redirect(settings.OIDC_LOGOUT_URL)


def avatar(request):
    """Serve the profile picture the login downloaded, out of the session holding it.

    Only for providers whose picture a browser cannot fetch for itself; see
    :mod:`sso.avatars`. Built from the requester's own session, and cached privately so no
    shared cache keeps one person's face for the next.

    Args:
        request: The HTTP request, whose session carries the picture.

    Returns:
        The picture, or 404 when the session carries none.
    """
    picture = stored_picture(request.session)
    if not picture:
        raise Http404("this session carries no profile picture")

    data, content_type = picture
    return HttpResponse(
        data,
        content_type=content_type,
        headers={"Cache-Control": f"private, max-age={PICTURE_MAX_AGE}"},
    )
