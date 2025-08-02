from django.urls import path, re_path
from django.views.generic.base import RedirectView
from .views import home, CategoryView, PostDetailView, search, download_quality, download_subtitle, TagDetailView, PageView, MediaListView, MediaDetailView
from . import views

urlpatterns = [
    path('', home, name='home'),
    path('search/', search, name='search'),

    re_path(
        r'^(?P<year>[0-9]{4})/(?P<month>[0-9]{2})/(?P<day>[0-9]{2})/(?P<slug>[\w-]+)/$',
        views.wordpress_redirect_view,
        name='wordpress_redirect'
    ),


    # ✅ CORRECTED: Use <str:slug> for flexibility
    path('category/<str:slug>/', CategoryView.as_view(), name='category'),
    path('tag/<str:slug>/', TagDetailView.as_view(), name='tag_detail'),
    
    path('download/quality/<int:pk>/', download_quality, name='download_quality'),
    path('download/subtitle/<int:pk>/', download_subtitle, name='download_subtitle'),

    # ✅ CORRECTED: Use <str:slug> for pages too
    path('<str:slug>/', PageView, name='page_view'),

    # ✅ This pattern is good, but let's make the post slug flexible too
    path('<str:category>/<str:slug>/', PostDetailView.as_view(), name='post_detail'),

    path('media/', MediaListView.as_view(), name='media_list'),
    path('media/<int:pk>/', MediaDetailView.as_view(), name='media_detail'),
]