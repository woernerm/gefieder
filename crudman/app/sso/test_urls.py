"""A URL configuration for testing the login redirect.

crudman/urls.py mounts allauth's views only when single sign-on is switched on, and the
suite runs with it off, so the name the redirect reverses does not exist there. Standing in
a route of the same name lets the view's branching be tested for what it is — a choice
between the provider and the local form — without reconfiguring the app registry.

That the real route is mounted when the setting is on is a wiring question, and is covered
by the integration suite instead.
"""
from django.http import HttpResponse
from django.urls import path

from crudman.urls import urlpatterns as real_urlpatterns

urlpatterns = real_urlpatterns + [
    path(
        "crudman/accounts/oidc/<str:provider_id>/login/",
        lambda request, provider_id: HttpResponse(),
        name="openid_connect_login",
    ),
]
