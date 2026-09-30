from django.urls import path

from . import views

app_name = "planner"

urlpatterns = [
    path("api/v1/route/", views.RouteView.as_view(), name="route"),
    path("api/v1/suggest/", views.suggest_view, name="suggest"),
    path("api/v1/health/", views.health, name="health"),
    path("map/", views.MapView.as_view(), name="map"),
    path("", views.MapView.as_view(), name="home"),
]
