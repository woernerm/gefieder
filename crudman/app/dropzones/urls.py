from django.urls import path

from . import views

app_name = "dropzones"

urlpatterns = [
    path("<uuid:token>/", views.upload, name="upload"),
    path("files/<int:pk>/download/", views.download, name="download"),
]
