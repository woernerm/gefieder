from django.urls import path

from . import views

app_name = "repository"

urlpatterns = [
    path("", views.VersionsView.as_view(), name="versions"),
    path("deploy/", views.deploy, name="deploy"),
]
