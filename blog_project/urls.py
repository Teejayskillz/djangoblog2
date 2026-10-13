"""
URL configuration for blog_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from core.sitemaps import PostSitemap, CategorySitemap, TagSitemap, StaticViewSitemap
from django.contrib.sitemaps.views import sitemap
from core.views import robots_txt ,  import_subscribers_view
from subscriptions.views import plan_list as pricing_view
from controlcenter.views import AdminRetiredRedirectView


sitemaps = {
    'blog': PostSitemap, 
    'categories': CategorySitemap,
    'tags': TagSitemap,
    'static': StaticViewSitemap,
}


urlpatterns = [
    # ---------------------------------------------------------------------
    # Default Django admin retired from normal use (2026-08).
    #
    # One pattern covers every shape of the public admin URL and forwards it to
    # the matching Control Center page (deep links keep working):
    #   /admin                        -> /control-center/
    #   /admin/                       -> /control-center/
    #   /admin/core/post/             -> /control-center/core/post/
    #   /admin/core/post/add/         -> /control-center/core/post/add/
    #   /admin/subscriptions/paymenttransaction/25/change/
    #                                 -> /control-center/subscriptions/paymenttransaction/25/change/
    # Admin-only routes with no Control Center equivalent (login, jsi18n, app
    # indexes, history, ...) fall back to the Control Center dashboard.
    #
    # The trailing-slash-less form is matched explicitly so CommonMiddleware
    # never issues its own permanent 301 APPEND_SLASH redirect for /admin.
    #
    # django.contrib.admin is still installed and still fully mounted below
    # under a private prefix, so admin templates, ModelAdmin registrations,
    # reverse('admin:...') lookups, permissions and migrations keep working.
    # ---------------------------------------------------------------------
    re_path(
        r'^admin(?:/(?P<subpath>.*))?$',
        AdminRetiredRedirectView.as_view(),
        name='admin_retired',
    ),
    # Built-in Django admin site code preserved (private, not linked publicly)
    path('django-admin-backend-internal/', admin.site.urls),
    path('control-center/', include('controlcenter.urls')),
    path('import-sub/', import_subscribers_view, name='import_subscribers'),
    path('media/<path:path>', serve, {'document_root': settings.MEDIA_ROOT}),
    path('ckeditor5/', include('django_ckeditor_5.urls')),
    path('robots.txt', robots_txt),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('accounts/', include('django.contrib.auth.urls')), 
    path('subscriptions/', include('subscriptions.urls')),
    path('pricing/', pricing_view, name='pricing'),
    path('sports/', include('sports.urls')),
    path('sports-streaming/', include('sports.urls')),
    path('', include('core.urls')), 
    path('ads/', include('ads.urls')),
] 
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT, show_indexes=settings.DEBUG)
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)