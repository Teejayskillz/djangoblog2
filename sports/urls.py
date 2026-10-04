from django.urls import path
from . import views

urlpatterns = [
    path('', views.sports_list, name='sports_list'),
    path('<slug:slug>/', views.sports_detail, name='sports_detail'),
]
