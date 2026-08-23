"""The panel data endpoint, fetched by HTMX once a placeholder is on the page."""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("<slug:slug>/data/", views.panel_data, name="data"),
]
