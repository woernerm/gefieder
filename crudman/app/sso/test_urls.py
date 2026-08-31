"""A URL configuration for testing the login redirect.

crudman/urls.py mounts allauth's views only with single sign-on on, so the name the
redirect reverses does not exist in a suite run with it off. Standing in a route of the
same name tests the view's branching without reconfiguring the app registry; that the
real route is mounted is covered by the integration suite.
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
