from django.urls import path

from . import views

app_name = "notebooks"

urlpatterns = [
    path("whoami/", views.whoami, name="whoami"),
    path("grafana/", views.grafana, name="grafana"),
]
