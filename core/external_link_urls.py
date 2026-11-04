"""URLs for the External Link Generator API (mounted under ``/api/external-link/``)."""

from django.urls import path

from . import external_link_views as views

app_name = "external_links"

urlpatterns = [
    path("", views.api_index, name="index"),
    path("connection-test/", views.connection_test, name="connection_test"),
    path("sessions/create/", views.session_create, name="session_create"),
    path("sessions/validate/", views.session_validate, name="session_validate"),
    path("sessions/redeem/", views.session_redeem, name="session_redeem"),
]
