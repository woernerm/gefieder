from django.urls import path

from . import views

app_name = "dbusers"

urlpatterns = [
    path("password/", views.password, name="password"),
]
