"""
URL configuration for crudman project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from sso import views as sso_views

# The base path under which the administration panel is served. It must match
# CRUDMAN_PATH of the proxy service. The proxy forwards this path unchanged, so that
# direct access on port 8000 uses the same URLs.
CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")

urlpatterns = [
    # The dropzone upload pages. Under CRUDMAN_PATH so the proxy needs no extra route,
    # but listed before the admin so its prefix pattern does not swallow them.
    path(f"{CRUDMAN_PATH}/dropzones/", include("dropzones.urls")),
    # The admin's own login address, claimed before admin.site.urls so that an
    # unauthenticated visitor can be sent to the identity provider instead of a form.
    # Django keeps redirecting here by name, so the interception needs no other change.
    path(f"{CRUDMAN_PATH}/login/", sso_views.login, name="login"),
    # Likewise the logout address, so signing out also ends the session at the provider.
    path(f"{CRUDMAN_PATH}/logout/", sso_views.logout, name="logout"),
    # Where a profile picture that only the provider's API would hand over is served from
    # afterwards. Before the admin's catch-all like the two above; registered whether or
    # not single sign-on is on, because a session without a picture simply has none.
    path(f"{CRUDMAN_PATH}/avatar/", sso_views.avatar, name="avatar"),
]

# allauth's own views, including the callback the provider redirects back to. Under
# CRUDMAN_PATH like everything else, so the registered redirect URI is
# https://<host>/<CRUDMAN_PATH>/accounts/oidc/<provider_id>/login/callback/
#
# Before the admin for the same reason the dropzones are: the admin's URLs end in a
# catch-all that matches anything under its prefix and sends anonymous visitors to the
# login page. Listed after it, every one of these addresses would come back here instead,
# and a sign-in would bounce between the two until the browser gave up.
if settings.OIDC_ENABLED:
    urlpatterns += [path(f"{CRUDMAN_PATH}/accounts/", include("allauth.urls"))]

urlpatterns += [
    path(f"{CRUDMAN_PATH}/", admin.site.urls),
    # Other URL paths
]
