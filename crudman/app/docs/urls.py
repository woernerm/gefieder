from django.urls import path

from . import views

app_name = "docs"

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("<slug:layer>/", views.LayerView.as_view(), name="layer"),
]
