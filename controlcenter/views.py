from django.shortcuts import render, redirect, get_object_or_404
from django.urls import Resolver404, resolve, reverse
from django.views import View
from django.views.generic import RedirectView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.apps import apps
from django.core.paginator import Paginator
from django.forms import modelform_factory
from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponse, Http404
from django.utils.text import capfirst
from django.utils import timezone
from django.db.models import Avg, Count, Max, Q, Sum
import datetime
from urllib.parse import urlencode

from django.conf import settings

from .models import (
    AnalyticsIssue,
    PageVisit,
    RawRequestStat,
    SlowRequestLog,
    ThrottleEvent,
    TRAFFIC_CLASSES,
)

from .models import PageVisit
from .slug_utils import apply_slug_defaults, get_slug_sources


class AdminRetiredRedirectView(RedirectView):
    """
    Retires the default Django admin from normal use, sending its URLs to the
    matching page of the custom Control Center with a temporary 302 redirect.

    The two URL layouts are deliberately parallel
    (``<app>/<model>/``, ``.../add/``, ``.../<pk>/change/``, ``.../<pk>/delete/``),
    so deep links keep working instead of dumping everyone on the dashboard::

        /admin/                                                   -> /control-center/
        /admin/core/post/                                         -> /control-center/core/post/
        /admin/core/post/add/                                     -> /control-center/core/post/add/
        /admin/subscriptions/paymenttransaction/25/change/         -> /control-center/subscriptions/paymenttransaction/25/change/
        /admin/subscriptions/paymenttransaction/25/delete/         -> /control-center/subscriptions/paymenttransaction/25/delete/

    Django-admin-only routes with no Control Center counterpart (``/admin/login/``,
    ``/admin/jsi18n/``, app indexes, ``.../<pk>/history/``, ...) fall back to the
    Control Center dashboard rather than 404ing. Targets are validated with the
    URL resolver, so the mapping follows the Control Center's own routes and
    never has to be kept in sync by hand.

    Design notes:
    * ``permanent = False`` -> 302, so the decision stays reversible and is
      never cached permanently by browsers, proxies or CDNs.
    * ``query_string = False`` -> stale admin query parameters are dropped
      instead of being appended to the Control Center URL.
    * All common HTTP methods redirect. An old bookmarked admin form that
      POSTs back to /admin/... must not raise 405/500; it lands on the
      Control Center instead.

    django.contrib.admin itself is untouched: the app stays installed, its
    models/ModelAdmin registrations stay registered, and its full URL set is
    still mounted under a private prefix in blog_project/urls.py so that
    ``reverse('admin:...')`` and a future rollback keep working.
    """

    permanent = False
    query_string = False
    fallback_url = '/control-center/'  # also used as the Control Center prefix

    def get_redirect_url(self, *args, **kwargs):
        subpath = (kwargs.get('subpath') or '').strip('/')
        if subpath:
            candidate = f'{self.fallback_url}{subpath}/'
            if self._is_control_center_route(candidate):
                return candidate
        return self.fallback_url

    @staticmethod
    def _is_control_center_route(path):
        """True when `path` is a live Control Center URL (not a dead end)."""
        try:
            match = resolve(path)
        except Resolver404:
            return False
        if 'controlcenter' not in (match.namespaces or ()):
            return False
        # Model routes are generic (<app_label>/<model_name>/...), so verify the
        # model exists -- otherwise the visitor would land on a Control Center 404.
        if 'app_label' in match.kwargs and 'model_name' in match.kwargs:
            try:
                apps.get_model(match.kwargs['app_label'], match.kwargs['model_name'])
            except LookupError:
                return False
        return True

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    put = post
    patch = post
    delete = post
    head = post


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_active and self.request.user.is_staff


def get_model_from_url(app_label, model_name):
    try:
        model = apps.get_model(app_label, model_name)
        return model
    except LookupError:
        return None


def get_model_icon(app_label, model_name):
    """Return appropriate dashicon-like emoji/symbol for WordPress sidebar."""
    icons = {
        'post': '📝',
        'category': '📁',
        'comment': '💬',
        'tag': '🏷️',
        'subscriber': '✉️',
        'media': '🖼️',
        'sportsmatch': '⚽',
        'subscriptionplan': '💳',
        'usersubscription': '⭐',
        'paymenttransaction': '💰',
        'ad': '📢',
        'user': '👤',
        'group': '👥',
        'pagevisit': '📊',
    }
    return icons.get(model_name.lower(), '📄')


def get_app_list_context(request):
    """
    Build structured navigation for the WordPress-style admin sidebar.
    Groups apps and models, filters models by user view permissions,
    and sorts prominent apps (Posts, Sports, Subscriptions, Ads) first.
    """
    app_list = []
    # Primary apps order
    app_order = {'core': 1, 'sports': 2, 'subscriptions': 3, 'ads': 4, 'auth': 5, 'controlcenter': 6}

    for app_config in sorted(apps.get_app_configs(), key=lambda a: app_order.get(a.label, 99)):
        # Skip internal or third party utility apps that have no registered models or should be hidden
        if app_config.label in ('sites', 'contenttypes', 'sessions', 'messages', 'staticfiles', 'taggit', 'django_cleanup', 'whitenoise', 'anymail'):
            continue

        models = []
        for model in app_config.get_models():
            # Check view or change permission
            model_name = model._meta.model_name
            perm_view = f"{app_config.label}.view_{model_name}"
            perm_change = f"{app_config.label}.change_{model_name}"
            if request.user.is_superuser or request.user.has_perm(perm_view) or request.user.has_perm(perm_change):
                models.append({
                    'name': model._meta.verbose_name_plural.title(),
                    'singular_name': model._meta.verbose_name.title(),
                    'model_name': model_name,
                    'app_label': app_config.label,
                    'icon': get_model_icon(app_config.label, model_name),
                })

        if models:
            app_list.append({
                'app_label': app_config.label,
                'name': app_config.verbose_name.title(),
                'models': sorted(models, key=lambda m: m['name']),
            })

    return {
        'app_list': app_list,
        'pending_comments_count': get_pending_comments_count(request),
    }


def get_pending_comments_count(request):
    """
    Number of comments waiting for approval, for the sidebar badge.

    Returns 0 for users who cannot moderate comments (and if the app is absent).
    """
    if not (request.user.is_superuser or request.user.has_perm('core.change_comment')):
        return 0
    try:
        Comment = apps.get_model('core', 'Comment')
        return Comment.objects.filter(is_approved=False).count()
    except LookupError:
        return 0


class DashboardView(StaffRequiredMixin, View):
    def get(self, request):
        # Query core models safely
        Post = apps.get_model('core', 'Post')
        Subscriber = apps.get_model('core', 'Subscriber')
        Comment = apps.get_model('core', 'Comment')
        SportsMatch = apps.get_model('sports', 'SportsMatch')
        UserSubscription = apps.get_model('subscriptions', 'UserSubscription')

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Human page views only: bots/crawlers are counted separately so the
        # headline "traffic" number is not inflated by crawlers or assets.
        today_page_views = PageVisit.objects.filter(
            timestamp__gte=today_start, traffic_class='human'
        ).count()
        today_visitors = PageVisit.objects.filter(
            timestamp__gte=today_start, traffic_class='human'
        ).values('visitor_id').distinct().count()
        today_bots = PageVisit.objects.filter(timestamp__gte=today_start).exclude(
            traffic_class='human'
        ).count()

        stats = {
            'posts': Post.objects.count() if Post else 0,
            'subscribers': Subscriber.objects.count() if Subscriber else 0,
            'comments': Comment.objects.count() if Comment else 0,
            'sports_matches': SportsMatch.objects.count() if SportsMatch else 0,
            'active_vip': UserSubscription.objects.filter(is_active=True, end_date__gt=now).count() if UserSubscription else 0,
            'today_views': today_page_views,
            'today_visitors': today_visitors,
            'today_bots': today_bots,
        }

        # Operational signals (24h) for the dashboard security card.
        last_24h = now - timezone.timedelta(hours=24)
        security = {
            'throttled_24h': ThrottleEvent.objects.filter(created_at__gte=last_24h).count(),
            'spam_7d': AnalyticsIssue.objects.filter(
                day__gte=(now - timezone.timedelta(days=7)).date(),
                kind__in=('comment_spam', 'comment_rejected'),
            ).aggregate(total=Sum('count'))['total'] or 0,
            'slow_24h': SlowRequestLog.objects.filter(created_at__gte=last_24h).count(),
            'tracking_errors_24h': AnalyticsIssue.objects.filter(
                day=now.date(), kind__in=('error', 'verify_failed', 'dedupe_store')
            ).aggregate(total=Sum('count'))['total'] or 0,
            'duplicates_24h': AnalyticsIssue.objects.filter(
                day=now.date(), kind='duplicate'
            ).aggregate(total=Sum('count'))['total'] or 0,
            'raw_24h': RawRequestStat.objects.filter(
                bucket_start__gte=last_24h
            ).aggregate(total=Sum('requests'))['total'] or 0,
        }

        recent_posts = Post.objects.order_by('-published_date')[:6] if Post else []
        recent_matches = SportsMatch.objects.order_by('-created_at')[:5] if SportsMatch else []
        recent_visits = PageVisit.objects.filter(traffic_class='human').order_by('-timestamp')[:8]

        context = {
            'stats': stats,
            'security': security,
            'recent_posts': recent_posts,
            'recent_matches': recent_matches,
            'recent_visits': recent_visits,
            'active_menu': 'dashboard',
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/dashboard.html', context)


class AnalyticsView(StaffRequiredMixin, View):
    """
    Dedicated Site Analytics view providing WordPress-style traffic reports:
    Daily trends, unique visitors, top pages, referrers, and device breakdown.
    """
    def get(self, request):
        now = timezone.now()
        range_param = request.GET.get('range', '7d')

        if range_param == 'today':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_count = 1
            range_label = 'Today'
        elif range_param == '30d':
            start_date = (now - datetime.timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            days_count = 30
            range_label = 'Last 30 Days'
        else: # default 7d
            start_date = (now - datetime.timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            days_count = 7
            range_label = 'Last 7 Days'

        visits_qs = PageVisit.objects.filter(timestamp__gte=start_date)

        total_views = visits_qs.count()
        unique_visitors = visits_qs.values('visitor_id').distinct().count()

        # Top 10 Pages
        top_pages = (
            visits_qs.values('path')
            .annotate(views=Count('id'), unique_visitors=Count('visitor_id', distinct=True))
            .order_by('-views')[:10]
        )

        # Top Referrers
        top_referrers = (
            visits_qs.exclude(referrer_domain__in=['', 'internal'])
            .values('referrer_domain')
            .annotate(hits=Count('id'))
            .order_by('-hits')[:10]
        )

        # Device Breakdown
        device_breakdown = (
            visits_qs.values('device_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        # Build day-by-day series for chart
        daily_stats = []
        for i in range(days_count - 1, -1, -1):
            day_target = (now - datetime.timedelta(days=i)).date()
            day_start = timezone.make_aware(datetime.datetime.combine(day_target, datetime.time.min))
            day_end = timezone.make_aware(datetime.datetime.combine(day_target, datetime.time.max))

            day_qs = visits_qs.filter(timestamp__range=(day_start, day_end))
            day_views = day_qs.count()
            day_visitors = day_qs.values('visitor_id').distinct().count()

            daily_stats.append({
                'date': day_target.strftime('%b %d'),
                'views': day_views,
                'visitors': day_visitors,
            })

        # Recent 25 raw visits (human only: the raw table below is for everyone)
        recent_visits = visits_qs.filter(traffic_class='human').order_by('-timestamp')[:25]

        # --- Traffic quality -------------------------------------------------
        class_rows = visits_qs.values('traffic_class').annotate(count=Count('id'))
        traffic_by_class = {row['traffic_class']: row['count'] for row in class_rows}
        for key, _label in TRAFFIC_CLASSES:
            traffic_by_class.setdefault(key, 0)

        traffic_class_rows = [
            {
                'key': key,
                'label': label,
                'count': traffic_by_class.get(key, 0),
                'share': round(traffic_by_class.get(key, 0) * 100 / total_views, 1) if total_views else 0,
            }
            for key, label in TRAFFIC_CLASSES
        ]

        human_qs = visits_qs.filter(traffic_class='human')
        human_views = human_qs.count()
        estimated_unique_visitors = human_qs.values('visitor_id').distinct().count()
        estimated_sessions = (
            human_qs.exclude(session_key__isnull=True)
            .exclude(session_key='')
            .values('session_key')
            .distinct()
            .count()
        )
        top_human_paths = (
            human_qs.values('path').annotate(hits=Count('id')).order_by('-hits')[:10]
        )

        # --- Raw server requests (from access-log import) ---------------------
        raw_in_range = RawRequestStat.objects.filter(bucket_start__gte=start_date)
        raw_requests = raw_in_range.aggregate(total=Sum('requests'))['total'] or 0
        raw_by_status = {
            row['status_class']: row['total']
            for row in raw_in_range.values('status_class').annotate(total=Sum('requests'))
        }
        top_raw_paths = (
            raw_in_range.values('path').annotate(total=Sum('requests')).order_by('-total')[:10]
        )

        # --- Operational signals ---------------------------------------------
        throttle_in_range = ThrottleEvent.objects.filter(created_at__gte=start_date)
        throttle_count = throttle_in_range.count()
        throttle_by_scope = {
            row['scope']: row['count']
            for row in throttle_in_range.values('scope').annotate(count=Count('id'))
        }
        recent_throttles = throttle_in_range.order_by('-created_at')[:10]

        issue_rows = AnalyticsIssue.objects.filter(day__gte=start_date.date())
        analytics_issues = list(issue_rows.order_by('-day', 'kind')[:12])
        issue_totals = {
            row['kind']: row['total']
            for row in issue_rows.values('kind').annotate(total=Sum('count'))
        }

        slow_qs = SlowRequestLog.objects.filter(created_at__gte=start_date)
        slow_summary = slow_qs.aggregate(
            count=Count('id'), avg_ms=Avg('duration_ms'), max_ms=Max('duration_ms')
        )
        slowest_views = slow_qs.order_by('-duration_ms')[:10]

        Comment = apps.get_model('core', 'Comment')
        comments_in_range = Comment.objects.filter(created_at__gte=start_date)
        comment_metrics = {
            'submitted': comments_in_range.count(),
            'approved': comments_in_range.filter(is_approved=True).count(),
            'pending': comments_in_range.filter(is_approved=False).count(),
            'rejected': issue_totals.get('comment_rejected', 0),
            'spam': issue_totals.get('comment_spam', 0),
        }

        PaymentTransaction = apps.get_model('subscriptions', 'PaymentTransaction')
        conversions = PaymentTransaction.objects.filter(
            created_at__gte=start_date, status='completed'
        ).count()

        context = {
            'range_param': range_param,
            'range_label': range_label,
            'total_views': total_views,
            'unique_visitors': unique_visitors,
            'top_pages': top_pages,
            'top_referrers': top_referrers,
            'device_breakdown': device_breakdown,
            'daily_stats': daily_stats,
            'recent_visits': recent_visits,
            # traffic quality
            'traffic_classes': TRAFFIC_CLASSES,
            'traffic_by_class': traffic_by_class,
            'traffic_class_rows': traffic_class_rows,
            'human_views': human_views,
            'estimated_unique_visitors': estimated_unique_visitors,
            'estimated_sessions': estimated_sessions,
            'top_human_paths': top_human_paths,
            # raw server-side truth
            'raw_requests': raw_requests,
            'raw_by_status': raw_by_status,
            'top_raw_paths': top_raw_paths,
            'raw_configured': bool(getattr(settings, 'ACCESS_LOG_PATH', '')),
            # operations
            'throttle_count': throttle_count,
            'throttle_by_scope': throttle_by_scope,
            'recent_throttles': recent_throttles,
            'analytics_issues': analytics_issues,
            'issue_totals': issue_totals,
            'slow_summary': slow_summary,
            'slowest_views': slowest_views,
            'comment_metrics': comment_metrics,
            'conversions': conversions,
            'active_menu': 'analytics',
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/analytics.html', context)


COMMENT_STATUS_FILTERS = {
    'pending': 'Pending',
    'approved': 'Approved',
    'all': 'All',
}


class CommentModerationView(StaffRequiredMixin, View):
    """
    Dedicated comment moderation screen.

    Staff with the `core.change_comment` permission can review every comment,
    approve it (makes it public on the site), unapprove it (hides it again) or
    delete it -- per comment or in bulk.
    """

    template_name = 'controlcenter/comments.html'
    per_page = 20
    change_permission = 'core.change_comment'
    delete_permission = 'core.delete_comment'

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _comment_model():
        return apps.get_model('core', 'Comment')

    def _can_moderate(self, request):
        return request.user.is_superuser or request.user.has_perm(self.change_permission)

    def _can_delete(self, request):
        return request.user.is_superuser or request.user.has_perm(self.delete_permission)

    def _counts(self):
        Comment = self._comment_model()
        return {
            'pending': Comment.objects.filter(is_approved=False).count(),
            'approved': Comment.objects.filter(is_approved=True).count(),
            'all': Comment.objects.count(),
        }

    def _filtered_queryset(self, request):
        data = request.POST if request.method == 'POST' else request.GET
        status = (data.get('status') or 'pending').strip()
        if status not in COMMENT_STATUS_FILTERS:
            status = 'pending'

        queryset = self._comment_model().objects.select_related(
            'post', 'user', 'user__subscription'
        )
        if status == 'pending':
            queryset = queryset.filter(is_approved=False)
        elif status == 'approved':
            queryset = queryset.filter(is_approved=True)

        query = (data.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(comment__icontains=query)
                | Q(name__icontains=query)
                | Q(user__username__icontains=query)
                | Q(post__title__icontains=query)
            )

        return queryset.order_by('-created_at'), status, query

    def _redirect_target(self, request):
        """Return to the same filtered view after an action."""
        next_url = (request.POST.get('next') or '').strip()
        # Only allow returning to this moderation screen (no open redirect).
        if next_url.startswith('/control-center/comments'):
            return next_url

        url = reverse('controlcenter:comments')
        params = {}
        status = (request.POST.get('status') or '').strip()
        if status in COMMENT_STATUS_FILTERS:
            params['status'] = status
        query = (request.POST.get('q') or '').strip()
        if query:
            params['q'] = query
        page = (request.POST.get('page') or '').strip()
        if page.isdigit() and page != '1':
            params['page'] = page
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    def _selected_ids(self, request):
        raw_ids = request.POST.getlist('selected')
        single = request.POST.get('comment_id')
        if single:
            raw_ids = [single]
        return [int(pk) for pk in raw_ids if str(pk).strip().isdigit()]

    # -- views -----------------------------------------------------------
    def get(self, request):
        if not self._can_moderate(request):
            return HttpResponseForbidden('You do not have permission to moderate comments.')

        queryset, status, query = self._filtered_queryset(request)
        paginator = Paginator(queryset, self.per_page)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'comments': page_obj.object_list,
            'page_obj': page_obj,
            'status': status,
            'q': query,
            'status_filters': COMMENT_STATUS_FILTERS,
            'counts': self._counts(),
            'can_delete': self._can_delete(request),
            'active_menu': 'comments',
        }
        context.update(get_app_list_context(request))
        return render(request, self.template_name, context)

    def post(self, request):
        if not self._can_moderate(request):
            return HttpResponseForbidden('You do not have permission to moderate comments.')

        action = (request.POST.get('action') or '').strip()
        ids = self._selected_ids(request)
        redirect_to = self._redirect_target(request)

        if not ids:
            messages.error(request, 'Please select at least one comment.')
            return redirect(redirect_to)

        queryset = self._comment_model().objects.filter(pk__in=ids)

        if action == 'approve':
            updated = queryset.update(is_approved=True)
            messages.success(
                request,
                f'{updated} comment{"" if updated == 1 else "s"} approved and now visible on the site.',
            )
        elif action == 'unapprove':
            updated = queryset.update(is_approved=False)
            messages.success(
                request,
                f'{updated} comment{"" if updated == 1 else "s"} unapproved and hidden from the site.',
            )
        elif action == 'delete':
            if not self._can_delete(request):
                messages.error(request, 'You do not have permission to delete comments.')
                return redirect(redirect_to)
            deleted, _details = queryset.delete()
            messages.success(
                request,
                f'{deleted} comment{"" if deleted == 1 else "s"} permanently deleted.',
            )
        else:
            messages.error(request, 'Unknown action.')

        return redirect(redirect_to)


class ModelListView(StaffRequiredMixin, View):
    def post(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        action = request.POST.get('action')
        selected = request.POST.getlist('selected')
        if action and selected:
            modeladmin = admin.site._registry.get(model)
            if modeladmin:
                actions = modeladmin.get_actions(request)
                if action in actions:
                    func, name, desc = actions[action]
                    queryset = model.objects.filter(pk__in=selected)
                    try:
                        func(modeladmin, request, queryset)
                        messages.success(request, f"Successfully executed '{desc}' on {queryset.count()} item(s).")
                    except Exception as e:
                        messages.error(request, f"Action failed: {e}")
            elif action == 'delete_selected':
                count = model.objects.filter(pk__in=selected).delete()[0]
                messages.success(request, f"Deleted {count} item(s).")

        query_string = request.META.get('QUERY_STRING', '')
        redirect_url = request.path + (f'?{query_string}' if query_string else '')
        return redirect(redirect_url)

    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)

        perm_view = f"{app_label}.view_{model._meta.model_name}"
        perm_change = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm_view) or request.user.has_perm(perm_change)):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
        if modeladmin and hasattr(modeladmin, 'get_queryset'):
            try:
                queryset = modeladmin.get_queryset(request)
            except Exception:
                queryset = model.objects.all()
        else:
            queryset = model.objects.all()

        # Search
        q = request.GET.get('q', '').strip()
        if q:
            search_query = Q()
            if modeladmin and getattr(modeladmin, 'search_fields', None):
                for sf in modeladmin.search_fields:
                    search_query |= Q(**{f'{sf}__icontains': q})
            else:
                for f in model._meta.fields:
                    if getattr(f, 'get_internal_type', lambda: '')() in ['CharField', 'TextField']:
                        search_query |= Q(**{f'{f.name}__icontains': q})
            queryset = queryset.filter(search_query).distinct()

        # Filtering by exact params
        active_filters = {}
        for key, value in request.GET.items():
            if key not in ('q', 'o', 'page') and value:
                try:
                    queryset = queryset.filter(**{key: value})
                    active_filters[key] = value
                except Exception:
                    pass

        # Ordering
        order = request.GET.get('o')
        if order:
            try:
                queryset = queryset.order_by(order)
            except Exception:
                pass
        elif modeladmin and getattr(modeladmin, 'ordering', None):
            try:
                queryset = queryset.order_by(*modeladmin.ordering)
            except Exception:
                pass

        # Ensure deterministic ordering for pagination
        try:
            if not getattr(queryset, 'ordered', False):
                queryset = queryset.order_by(f"-{model._meta.pk.name}")
        except Exception:
            pass

        paginator = Paginator(queryset, 20)
        page = request.GET.get('page')
        page_obj = paginator.get_page(page)

        # Build list_display columns
        display_columns = []
        if modeladmin and getattr(modeladmin, 'list_display', None):
            raw_list = list(modeladmin.list_display)
        else:
            raw_list = [f.name for f in model._meta.fields if f.name not in ('id', 'password', 'content')][:6]

        for item in raw_list:
            col = {'label': None, 'name': None, 'type': 'field'}
            if callable(item):
                name = getattr(item, '__name__', 'custom')
                label = getattr(item, 'short_description', name.replace('_', ' ').title())
                col.update({'label': label, 'name': name, 'type': 'callable'})
            elif isinstance(item, str):
                if modeladmin and hasattr(modeladmin, item) and callable(getattr(modeladmin, item)):
                    attr_func = getattr(modeladmin, item)
                    label = getattr(attr_func, 'short_description', item.replace('_', ' ').title())
                    col.update({'label': label, 'name': item, 'type': 'callable'})
                else:
                    col.update({'label': item.replace('_', ' ').title(), 'name': item, 'type': 'field'})
            else:
                col.update({'label': str(item).title(), 'name': str(item), 'type': 'field'})
            display_columns.append(col)

        # Available bulk actions
        actions = []
        if modeladmin:
            try:
                acts = modeladmin.get_actions(request)
                actions = [(k, v[2]) for k, v in acts.items()]
            except Exception:
                actions = []
        if not actions:
            actions = [('delete_selected', 'Delete selected items')]

        # Build filter options for sidebar/dropdowns
        filter_specs = []
        if modeladmin:
            for lf in getattr(modeladmin, 'list_filter', ()):
                if isinstance(lf, str):
                    try:
                        vals = list(model.objects.order_by(lf).values_list(lf, flat=True).distinct()[:50])
                        filter_specs.append({
                            'field': lf,
                            'title': lf.replace('_', ' ').title(),
                            'values': [v for v in vals if v is not None and v != ''],
                            'current': request.GET.get(lf, '')
                        })
                    except Exception:
                        continue

        opts = model._meta
        context = {
            'model': model,
            'opts': opts,
            'app_label': app_label,
            'model_name': model_name,
            'page_obj': page_obj,
            'display_columns': display_columns,
            'q': q,
            'actions': actions,
            'filter_specs': filter_specs,
            'active_filters': active_filters,
            'modeladmin': modeladmin,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/list.html', context)


class ModelCreateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')

        form = form_class()

        # Inline formsets
        inline_formsets = []
        if modeladmin:
            for inline in modeladmin.get_inline_instances(request):
                try:
                    FormSet = inline.get_formset(request)
                    prefix = f"{inline.opts.model._meta.model_name}_set"
                    fs = FormSet(prefix=prefix)
                    inline_formsets.append({
                        'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                        'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                        'formset': fs,
                        'prefix': prefix,
                    })
                except Exception:
                    pass

        opts = model._meta
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'action': 'Add New',
            'inline_formsets': inline_formsets,
            'object': None,
            'slug_sources': get_slug_sources(model, modeladmin=modeladmin, request=request),
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)

    def post(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')

        # Fill slug fields left blank (the Django admin does this with
        # prepopulated_fields + prepopulate.js, which the Control Center's own
        # templates never loaded).
        data = apply_slug_defaults(model, request.POST, modeladmin=modeladmin, request=request)
        form = form_class(data, request.FILES)
        inline_formsets = []
        inline_instances = modeladmin.get_inline_instances(request) if modeladmin else []
        for inline in inline_instances:
            try:
                FormSet = inline.get_formset(request)
                prefix = f"{inline.opts.model._meta.model_name}_set"
                fs = FormSet(request.POST, request.FILES, prefix=prefix)
                inline_formsets.append((inline, fs))
            except Exception:
                pass

        if form.is_valid() and all(fs.is_valid() for _, fs in inline_formsets):
            obj = form.save(commit=False)
            if modeladmin and hasattr(modeladmin, 'save_model'):
                modeladmin.save_model(request, obj, form, False)
            else:
                obj.save()
            form.save_m2m()

            for inline, fs in inline_formsets:
                fs.instance = obj
                fs.save()

            if modeladmin and hasattr(modeladmin, 'save_related'):
                try:
                    modeladmin.save_related(request, form, [fs for _, fs in inline_formsets], False)
                except Exception:
                    pass

            messages.success(request, f"{model._meta.verbose_name.title()} was created successfully.")
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))

        opts = model._meta
        rendered_inlines = []
        for inline, fs in inline_formsets:
            rendered_inlines.append({
                'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                'formset': fs,
                'prefix': fs.prefix,
            })
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'action': 'Add New',
            'inline_formsets': rendered_inlines,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelUpdateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request, obj)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')

        form = form_class(instance=obj)
        inline_formsets = []
        if modeladmin:
            for inline in modeladmin.get_inline_instances(request, obj):
                try:
                    FormSet = inline.get_formset(request, obj)
                    prefix = f"{inline.opts.model._meta.model_name}_set"
                    fs = FormSet(instance=obj, prefix=prefix)
                    inline_formsets.append({
                        'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                        'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                        'formset': fs,
                        'prefix': prefix,
                    })
                except Exception:
                    pass

        opts = model._meta
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'object': obj,
            'action': 'Edit',
            'inline_formsets': inline_formsets,
            'slug_sources': get_slug_sources(model, modeladmin=modeladmin, request=request, obj=obj),
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)

    def post(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request, obj)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')

        # Keep slug auto-generation working on edits too (blank slug -> rebuild
        # from the source field, excluding this row from the uniqueness check).
        data = apply_slug_defaults(
            model, request.POST, modeladmin=modeladmin, request=request, instance=obj
        )
        form = form_class(data, request.FILES, instance=obj)
        inline_formsets = []
        inline_instances = modeladmin.get_inline_instances(request, obj) if modeladmin else []
        for inline in inline_instances:
            try:
                FormSet = inline.get_formset(request, obj)
                prefix = f"{inline.opts.model._meta.model_name}_set"
                fs = FormSet(request.POST, request.FILES, instance=obj, prefix=prefix)
                inline_formsets.append((inline, fs))
            except Exception:
                pass

        if form.is_valid() and all(fs.is_valid() for _, fs in inline_formsets):
            if modeladmin and hasattr(modeladmin, 'save_model'):
                modeladmin.save_model(request, form.instance, form, True)
            else:
                form.save()

            for inline, fs in inline_formsets:
                fs.instance = form.instance
                fs.save()

            if modeladmin and hasattr(modeladmin, 'save_related'):
                try:
                    modeladmin.save_related(request, form, [fs for _, fs in inline_formsets], True)
                except Exception:
                    pass

            messages.success(request, f"{model._meta.verbose_name.title()} was updated successfully.")
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))

        opts = model._meta
        rendered_inlines = []
        for inline, fs in inline_formsets:
            rendered_inlines.append({
                'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                'formset': fs,
                'prefix': fs.prefix,
            })
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'object': obj,
            'action': 'Edit',
            'inline_formsets': rendered_inlines,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelDetailView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.view_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        opts = model._meta

        fields_data = []
        for f in model._meta.fields:
            val = getattr(obj, f.name, '')
            fields_data.append({
                'label': f.verbose_name.title(),
                'value': val,
                'name': f.name,
            })

        context = {
            'model': model,
            'opts': opts,
            'object': obj,
            'fields_data': fields_data,
            'modeladmin': modeladmin,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/detail.html', context)


class ModelDeleteView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        opts = model._meta
        context = {
            'object': obj,
            'model': model,
            'opts': opts,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/confirm_delete.html', context)

    def post(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        name = str(obj)
        obj.delete()
        messages.success(request, f"Successfully deleted '{name}'.")
        return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))


class LogoutStaffView(View):
    """Logs out staff user and redirects to login."""
    def get(self, request):
        from django.contrib.auth import logout
        logout(request)
        return redirect('/accounts/login/?next=/control-center/')

    def post(self, request):
        from django.contrib.auth import logout
        logout(request)
        return redirect('/accounts/login/?next=/control-center/')
