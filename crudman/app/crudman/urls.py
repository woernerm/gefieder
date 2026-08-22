"""URL configuration for the crudman project.

Everything is served under CRUDMAN_PATH, and order matters: the admin's URLs end in a
catch-all that matches anything under its prefix, so every other route is listed first.
"""
import os

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from sso import views as sso_views

# Must match CRUDMAN_PATH of the proxy service, which forwards the path unchanged so
# that direct access on port 8000 uses the same URLs.
CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")

urlpatterns = [
    # Under CRUDMAN_PATH so the proxy needs no extra route.
    path(f"{CRUDMAN_PATH}/dropzones/", include("dropzones.urls")),
    # The fragments each chart panel fetches for itself once the page is up.
    path(f"{CRUDMAN_PATH}/panels/", include("panels.urls")),
    # The admin's own login address, claimed so an unauthenticated visitor is sent to the
    # identity provider instead of a form. Django keeps redirecting here by name, so the
    # interception needs no other change.
    path(f"{CRUDMAN_PATH}/login/", sso_views.login, name="login"),
    # Likewise the logout address, so signing out also ends the session at the provider.
    path(f"{CRUDMAN_PATH}/logout/", sso_views.logout, name="logout"),
    # Where a profile picture only the provider's API would hand over is served from
    # afterwards. Registered whether or not single sign-on is on, because a session
    # without a picture simply has none.
    path(f"{CRUDMAN_PATH}/avatar/", sso_views.avatar, name="avatar"),
]

# allauth's own views, including the callback the provider redirects back to, so the
# registered redirect URI is
# https://<host>/<CRUDMAN_PATH>/accounts/oidc/<provider_id>/login/callback/
#
# Listed after the admin, these would hit its catch-all and come back to the login page,
# and a sign-in would bounce between the two until the browser gave up.
if settings.OIDC_ENABLED:
    urlpatterns += [path(f"{CRUDMAN_PATH}/accounts/", include("allauth.urls"))]

urlpatterns += [
    path(f"{CRUDMAN_PATH}/", admin.site.urls),
]
