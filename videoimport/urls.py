"""URLs for the control-center video import panel."""

from django.urls import path

from videoimport import views

app_name = "videoimport"

urlpatterns = [
    path("preview/", views.api_preview, name="preview"),
    path("create/", views.api_create, name="create"),
    path("status/<str:public_id>/", views.api_status, name="status"),
    path("cancel/<str:public_id>/", views.api_cancel, name="cancel"),
    path("retry/<str:public_id>/", views.api_retry, name="retry"),
    path("attach/", views.api_attach, name="attach"),
]
