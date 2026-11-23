from django.urls import path

from . import views

app_name = "system"

urlpatterns = [
    path("decide/", views.decide, name="decide"),
]
