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

from django.contrib import admin
from django.urls import include, path

# The base path under which the administration panel is served. It must match
# CRUDMAN_PATH of the proxy service. The proxy forwards this path unchanged, so that
# direct access on port 8000 uses the same URLs.
CRUDMAN_PATH = os.environ.get("CRUDMAN_PATH", "crudman")

urlpatterns = [
    # The dropzone upload pages. Under CRUDMAN_PATH so the proxy needs no extra route,
    # but listed before the admin so its prefix pattern does not swallow them.
    path(f"{CRUDMAN_PATH}/dropzones/", include("dropzones.urls")),
    path(f"{CRUDMAN_PATH}/", admin.site.urls),
    # Other URL paths
]
