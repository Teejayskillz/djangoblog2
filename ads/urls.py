# ads/urls.py
from django.urls import path
from . import views

app_name = 'ads' # Namespace for your ad app URLs

urlpatterns = [
    # URL for tracking ad clicks
    path('click/<int:pk>/', views.ad_click_track, name='ad_click'),
]
