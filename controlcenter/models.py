import hashlib

from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Traffic classification vocabulary
# ---------------------------------------------------------------------------
# Deliberately conservative: traffic we cannot prove is human is NOT counted as
# human. "unknown" exists so we never have to guess between human and bot.
TRAFFIC_CLASSES = [
    ('human', 'Estimated human'),
    ('verified_bot', 'Verified search crawler'),
    ('known_bot', 'Known bot (unverified)'),
    ('suspected_bot', 'Suspected automated'),
    ('unknown', 'Unknown'),
]


class PageVisit(models.Model):
    """
    Lightweight first-party page-view record (server side only).

    Only HTML page views are stored here: static assets, health checks and the
    admin/control-center are filtered out before this row is written, so the
    dashboard's "page views" metric is not inflated by asset traffic.
    """
    DEVICE_CHOICES = [
        ('desktop', 'Desktop'),
        ('mobile', 'Mobile'),
        ('tablet', 'Tablet'),
        ('bot', 'Bot/Crawler'),
        ('other', 'Other'),
    ]

    path = models.CharField(max_length=500, db_index=True)
    method = models.CharField(max_length=10, default='GET')
    status_code = models.PositiveSmallIntegerField(default=200)

    # Anonymized visitor identifier (salted daily hash) for unique estimates
    visitor_id = models.CharField(max_length=64, db_index=True)
    session_key = models.CharField(max_length=40, blank=True, null=True, db_index=True)

    # Traffic attribution
    referrer = models.CharField(max_length=500, blank=True, default='')
    referrer_domain = models.CharField(max_length=150, blank=True, default='', db_index=True)

    # Device / environment breakdown
    device_type = models.CharField(max_length=20, choices=DEVICE_CHOICES, default='desktop', db_index=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')

    # Traffic classification (see TRAFFIC_CLASSES) and crawler identity
    traffic_class = models.CharField(
        max_length=20, choices=TRAFFIC_CLASSES, default='unknown', db_index=True
    )
    bot_name = models.CharField(max_length=40, blank=True, default='')
    is_page_view = models.BooleanField(default=True, db_index=True)
    # Hash of (visitor, path, time bucket) used to drop duplicate events.
    dedupe_key = models.CharField(max_length=64, blank=True, default='', db_index=True)

    # User status
    is_authenticated = models.BooleanField(default=False)
    is_staff_user = models.BooleanField(default=False)

    # Timestamp with index for fast date range filtering
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = 'Page Visit'
        verbose_name_plural = 'Page Visits'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'path']),
            models.Index(fields=['timestamp', 'referrer_domain']),
            models.Index(fields=['timestamp', 'device_type']),
            models.Index(fields=['is_page_view', 'traffic_class', 'timestamp']),
            models.Index(fields=['traffic_class', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.path} ({self.timestamp.strftime('%Y-%m-%d %H:%M')})"


class ThrottleCounter(models.Model):
    """
    Fixed-window hit counter used by controlcenter.throttle.

    One row per (scope, key, window_start). Incremented with a single UPDATE so
    concurrent requests cannot lose hits; expired rows are pruned periodically.
    """
    scope = models.CharField(max_length=40, db_index=True)
    key_hash = models.CharField(max_length=64)
    window_start = models.DateTimeField()
    hits = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Throttle Counter'
        verbose_name_plural = 'Throttle Counters'
        constraints = [
            models.UniqueConstraint(
                fields=['scope', 'key_hash', 'window_start'],
                name='cc_throttle_unique_window',
            ),
        ]
        indexes = [
            models.Index(fields=['window_start']),
        ]

    def __str__(self):
        return f'{self.scope} {self.key_hash[:8]} {self.window_start:%H:%M} ({self.hits})'


class ThrottleEvent(models.Model):
    """
    A rate-limit event: used for monitoring, not for enforcement.

    Only written when a limit is actually hit (and de-duplicated per block), so
    normal traffic causes no extra rows.
    """
    ACTIONS = [
        ('throttled', 'Temporarily throttled'),
    ]

    scope = models.CharField(max_length=40, db_index=True)
    action = models.CharField(max_length=20, choices=ACTIONS, default='throttled')
    path = models.CharField(max_length=255, blank=True, default='')
    ip_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    key_hash = models.CharField(max_length=64, blank=True, default='')
    user_id = models.PositiveIntegerField(null=True, blank=True)
    retry_after = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = 'Throttle Event'
        verbose_name_plural = 'Throttle Events'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at', 'scope']),
        ]

    def __str__(self):
        return f'{self.scope} blocked at {self.created_at:%Y-%m-%d %H:%M}'


class RawRequestStat(models.Model):
    """
    Aggregated raw HTTP request counts imported from the web server access log.

    Django never sees requests that the web server rejects outright, so this
    table is the only honest source for "raw requests" and for server-level 4xx
    /5xx rates. Populated by `manage.py import_access_log`.
    """
    bucket_start = models.DateTimeField(db_index=True)
    path = models.CharField(max_length=255, db_index=True)
    method = models.CharField(max_length=10, default='GET')
    status_class = models.CharField(max_length=8, default='2xx')
    requests = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Raw Request Stat'
        verbose_name_plural = 'Raw Request Stats'
        ordering = ['-bucket_start']
        constraints = [
            models.UniqueConstraint(
                fields=['bucket_start', 'path', 'method', 'status_class'],
                name='cc_rawreq_unique_bucket',
            ),
        ]
        indexes = [
            models.Index(fields=['bucket_start', 'requests']),
        ]

    def __str__(self):
        return f'{self.bucket_start:%Y-%m-%d %H:%M} {self.method} {self.path} x{self.requests}'


class SlowRequestLog(models.Model):
    """
    Requests slower than ANALYTICS_SLOW_REQUEST_MS.

    Written per slow request (rare), so normal traffic costs nothing.
    """
    path = models.CharField(max_length=255, db_index=True)
    view_name = models.CharField(max_length=120, blank=True, default='')
    duration_ms = models.PositiveIntegerField(default=0)
    status_code = models.PositiveSmallIntegerField(default=200)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = 'Slow Request'
        verbose_name_plural = 'Slow Requests'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at', 'duration_ms']),
        ]

    def __str__(self):
        return f'{self.path} {self.duration_ms}ms'


class AnalyticsIssue(models.Model):
    """
    Analytics processing problems, aggregated per day.

    Covers tracking failures, duplicate page views that were dropped, and
    crawler verification failures, so problems are visible in the dashboard
    instead of being silently swallowed.
    """
    KINDS = [
        ('error', 'Tracking error'),
        ('duplicate', 'Duplicate page view dropped'),
        ('verify_failed', 'Crawler verification failed'),
        ('dedupe_store', 'Dedupe store unavailable'),
        ('comment_rejected', 'Comment submission rejected'),
        ('comment_spam', 'Comment blocked as spam'),
    ]

    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    day = models.DateField(db_index=True)
    count = models.PositiveIntegerField(default=1)
    last_detail = models.CharField(max_length=200, blank=True, default='')
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Analytics Issue'
        verbose_name_plural = 'Analytics Issues'
        ordering = ['-day', 'kind']
        constraints = [
            models.UniqueConstraint(fields=['kind', 'day'], name='cc_analytics_issue_unique'),
        ]

    def __str__(self):
        return f'{self.kind} on {self.day} ({self.count})'


def hash_value(value):
    """Stable, non-reversible hash used for IPs and throttle keys."""
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:64]
