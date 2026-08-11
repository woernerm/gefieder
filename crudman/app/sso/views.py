"""The admin's login and logout URLs, which route both through the identity provider."""
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import logout as end_session
from django.shortcuts import redirect
from django.urls import reverse

# Adding this to the login URL reaches the local form while single sign-on is on. It is the
# way back in for the superuser when the provider is unreachable or misconfigured, so it is
# named in the README; Grafana's equivalent is /login?disableAutoLogin.
LOCAL_LOGIN_PARAM = "local"


def login(request):
    """Redirect to the provider, or show the local form.

    A visitor who still has a session with the provider is signed in without seeing a page
    at all, which is the point of doing this instead of offering a button.

    A POST is always the local form submitting itself: the form posts back to this URL
    without the query string, so treating POST as local is what keeps the escape hatch
    usable at all.
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

    Without the second half, signing out achieves nothing visible: the provider still holds
    its session, and the redirect above hands it straight back on the next page view. The
    person appears to be signed in again a moment after asking not to be.

    Anything but a POST is left to the admin, which answers a GET here with "method not
    allowed" -- signing someone out is a change, and Django stopped accepting it from a
    link so that another site cannot trigger it.
    """
    signs_out_at_provider = settings.OIDC_ENABLED and settings.OIDC_LOGOUT_URL
    if request.method != "POST" or not signs_out_at_provider:
        return admin.site.logout(request)

    end_session(request)
    return redirect(settings.OIDC_LOGOUT_URL)
