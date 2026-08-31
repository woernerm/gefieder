"""URL configuration for the crudman project.

Everything is served under CRUDMAN_PATH, and order matters: the admin ends in a catch-all
that matches anything under its prefix, so every other route is listed first.
"""
import os

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from sso import views as sso_views

from . import views

# Must match CRUDMAN_PATH of the proxy, which forwards the path unchanged so that direct
# access on port 8000 uses the same URLs.
CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")

urlpatterns = [
    # Under CRUDMAN_PATH so the proxy needs no extra route.
    path(f"{CRUDMAN_PATH}/dropzones/", include("dropzones.urls")),
    # The SQLMesh model documentation, open from the viewer rank up rather than to staff
    # only.
    path(f"{CRUDMAN_PATH}/docs/", include("docs.urls")),
    # The admin's own login address, claimed so an unauthenticated visitor is sent to the
    # identity provider instead of a form. Django redirects here by name, so nothing else
    # needs to change.
    path(f"{CRUDMAN_PATH}/login/", sso_views.login, name="login"),
    # Likewise the logout address, so signing out also ends the session at the provider.
    path(f"{CRUDMAN_PATH}/logout/", sso_views.logout, name="logout"),
    # Serves the profile picture fetched from the provider. Registered whether or not
    # single sign-on is on: a session without a picture simply has none.
    path(f"{CRUDMAN_PATH}/avatar/", sso_views.avatar, name="avatar"),
]

# allauth's own views, including the callback the provider redirects back to, so the
# registered redirect URI is
# https://<host>/<CRUDMAN_PATH>/accounts/oidc/<provider_id>/login/callback/
# Listed after the admin they would hit its catch-all, and a sign-in would bounce between
# the two until the browser gave up.
if settings.OIDC_ENABLED:
    urlpatterns += [path(f"{CRUDMAN_PATH}/accounts/", include("allauth.urls"))]

# A route that raises on purpose, so the integration suite can check that a 500's
# traceback reaches the journal: only the container's main process is forwarded there, so
# a "podman exec" cannot show it. Enabled by the test stack alone, since an always-present
# route that raises is one URL guess away from being a nuisance.
if os.environ.get("ERROR_LOGGING_PROBE", "").strip().lower() == "true":
    urlpatterns += [
        path(f"{CRUDMAN_PATH}/error-logging-probe/", views.error_logging_probe),
    ]

urlpatterns += [
    path(f"{CRUDMAN_PATH}/", admin.site.urls),
]
