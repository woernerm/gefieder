from django.urls import path

from . import views

app_name = "dropzones"

urlpatterns = [
    path("<uuid:token>/", views.upload, name="upload"),
    path("api/<uuid:token>/", views.api_upload, name="api_upload"),
    path("webhook/<uuid:token>/", views.webhook_upload, name="webhook_upload"),
    path("files/<int:pk>/download/", views.download, name="download"),
]
