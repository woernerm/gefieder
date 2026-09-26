from django.urls import path

from . import views

app_name = "dashboards"

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("<slug:name>/", views.DashboardView.as_view(), name="dashboard"),
    path("<slug:name>/<int:index>/", views.PanelView.as_view(), name="panel"),
    path("<slug:name>/<int:index>.xlsx", views.DownloadView.as_view(), name="download"),
]
