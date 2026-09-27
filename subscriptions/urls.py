from django.urls import path
from . import views

app_name = 'subscriptions'

urlpatterns = [
    path('', views.plan_list, name='plan_list'),
    path('checkout/<slug:plan_slug>/', views.checkout, name='checkout'),
    path('profile/', views.profile, name='profile'),
]
