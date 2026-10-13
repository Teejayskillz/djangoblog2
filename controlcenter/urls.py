from django.urls import path
from . import views

app_name = 'controlcenter'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('analytics/', views.AnalyticsView.as_view(), name='analytics'),
    path('<str:app_label>/<str:model_name>/', views.ModelListView.as_view(), name='model_list'),
    path('<str:app_label>/<str:model_name>/add/', views.ModelCreateView.as_view(), name='model_add'),
    path('<str:app_label>/<str:model_name>/<int:pk>/view/', views.ModelDetailView.as_view(), name='model_view'),
    path('<str:app_label>/<str:model_name>/<int:pk>/change/', views.ModelUpdateView.as_view(), name='model_change'),
    path('<str:app_label>/<str:model_name>/<int:pk>/delete/', views.ModelDeleteView.as_view(), name='model_delete'),
    path('logout/', views.LogoutStaffView.as_view(), name='logout'),
]
