"""
Analytics + operational monitoring middleware.

Responsibilities
----------------
1. Record *HTML page views* only (never static assets, never the admin or the
   Control Center, never health checks), classified as human / verified crawler
   / known bot / suspected bot / unknown.
2. Drop duplicate events for the same visitor+path within a short window, so a
   page cannot be counted twice (e.g. prefetch + navigation, or a retry).
3. Record requests slower than a configurable threshold for the dashboard.
4. Never break a request: every failure is caught and only logged / aggregated.

Raw HTTP request totals are *not* produced here -- Django cannot see requests
its own web server rejects. Those come from `manage.py import_access_log`.
"""

import hashlib
import logging
import time

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from . import traffic
from .models import PageVisit

logger = logging.getLogger(__name__)


def record_issue(kind, detail=''):
    """Aggregate an analytics problem per day (safe to call from anywhere)."""
    from .models import AnalyticsIssue

    try:
        today = timezone.now().date()
        updated = AnalyticsIssue.objects.filter(kind=kind, day=today).update(
            count=F('count') + 1,
            last_seen=timezone.now(),
            last_detail=str(detail)[:200],
        )
        if not updated:
            AnalyticsIssue.objects.create(
                kind=kind, day=today, count=1, last_detail=str(detail)[:200]
            )
    except Exception:  # pragma: no cover - monitoring must never break requests
        logger.exception('Could not record analytics issue %s', kind)


class TrafficAnalyticsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.slow_threshold_ms = int(
            getattr(settings, 'ANALYTICS_SLOW_REQUEST_MS', 800)
        )

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            self.process(request, response, duration_ms)
        except Exception:
            logger.exception(
                'Traffic analytics failed for %s', getattr(request, 'path', '?')
            )
            record_issue('error', getattr(request, 'path', ''))

        return response

    # -- internals -------------------------------------------------------
    def process(self, request, response, duration_ms):
        path = getattr(request, 'path', '') or ''

        # Static assets, admin, Control Center, health checks: no analytics rows,
        # no classification work, no database writes.
        if traffic.is_excluded_path(path) or traffic.is_static_path(path):
            return

        if duration_ms >= self.slow_threshold_ms:
            self.record_slow_request(request, response, duration_ms)

        if request.method not in ('GET', 'HEAD'):
            return
        if path != '/' and not path.endswith('/'):
            # API-ish/internal endpoints are not page views.
            return
        if response.status_code >= 400:
            return
        if not traffic.is_html_response(response):
            return
        if not traffic.accepts_html(request):
            return

        self.record_page_view(request, response)

    def record_slow_request(self, request, response, duration_ms):
        from .models import SlowRequestLog

        match = getattr(request, 'resolver_match', None)
        view_name = ''
        if match is not None:
            view_name = ':'.join(
                [part for part in (match.namespace, match.url_name) if part]
            )[:120]
        SlowRequestLog.objects.create(
            path=(request.path or '')[:255],
            view_name=view_name,
            duration_ms=duration_ms,
            status_code=getattr(response, 'status_code', 200) or 200,
        )

    def record_page_view(self, request, response):
        ip = traffic.get_client_ip(request)
        user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:300]
        now = timezone.now()
        visitor_id = traffic.visitor_hash(ip, user_agent)
        classification = traffic.classify_request(request, ip=ip)

        # Deduplicate identical visitor+path events inside the window.
        window = int(getattr(settings, 'ANALYTICS_DEDUPE_SECONDS', 60))
        bucket = int(now.timestamp()) // max(1, window)
        dedupe_key = hashlib.sha256(
            f'{visitor_id}|{request.path}|{bucket}'.encode('utf-8')
        ).hexdigest()[:64]

        if PageVisit.objects.filter(
            dedupe_key=dedupe_key,
            timestamp__gte=now - timezone.timedelta(seconds=window),
        ).exists():
            record_issue('duplicate', request.path)
            return

        referrer, domain = traffic.referrer_domain(request)
        session_key = ''
        try:
            # Reading session_key does not create a session for anonymous users.
            session_key = (request.session.session_key or '')[:40]
        except Exception:
            session_key = ''

        user = getattr(request, 'user', None)
        is_authenticated = bool(user and user.is_authenticated)

        PageVisit.objects.create(
            path=(request.path or '')[:500],
            method=request.method,
            status_code=response.status_code,
            visitor_id=visitor_id,
            session_key=session_key or None,
            referrer=referrer,
            referrer_domain=domain,
            device_type=classification['device_type'],
            user_agent=user_agent,
            traffic_class=classification['traffic_class'],
            bot_name=classification['bot_name'],
            is_page_view=True,
            dedupe_key=dedupe_key,
            is_authenticated=is_authenticated,
            is_staff_user=bool(is_authenticated and user.is_staff),
            timestamp=now,
        )
