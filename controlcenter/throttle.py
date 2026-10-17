"""
Database-backed rate limiting for abuse-prone endpoints (no Redis required).

How it works
------------
* A fixed window counter per (scope, key, window_start) is incremented with a
  single atomic UPDATE (or INSERT on first hit), so concurrent requests cannot
  lose hits and MySQL/SQLite handle it natively.
* When a limit is exceeded the key is blocked *in the cache* for a configurable
  cooldown, so the block is enforced without a query on every follow-up request
  and no counter row grows unboundedly. Blocks are temporary by design.
* A ThrottleEvent row is written only when a limit is actually hit, which keeps
  monitoring data cheap.
* Nothing here touches static assets: throttling is opt-in per view/scope.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    hits: int
    window: int      # seconds
    cooldown: int    # seconds the caller is blocked after exceeding `hits`

    @property
    def retry_after(self):
        return self.cooldown or self.window


# Defaults can be overridden through settings.RATE_LIMITS (env-configurable).
DEFAULT_LIMITS = {
    'login': Limit(hits=20, window=300, cooldown=300),
    'login_username': Limit(hits=10, window=900, cooldown=900),
    'register': Limit(hits=5, window=3600, cooldown=3600),
    'password_reset': Limit(hits=5, window=3600, cooldown=3600),
    'comment': Limit(hits=10, window=3600, cooldown=1800),
    'search': Limit(hits=60, window=300, cooldown=120),
    'download': Limit(hits=30, window=300, cooldown=300),
    'activation': Limit(hits=10, window=3600, cooldown=3600),
}


@dataclass
class ThrottleResult:
    allowed: bool
    scope: str
    retry_after: int = 0
    hits: int = 0
    limit: int = 0

    def as_dict(self):
        return {
            'allowed': self.allowed,
            'scope': self.scope,
            'retry_after': self.retry_after,
            'hits': self.hits,
            'limit': self.limit,
        }


def get_limit(scope):
    configured = getattr(settings, 'RATE_LIMITS', None) or {}
    if scope in configured:
        value = configured[scope]
        if isinstance(value, Limit):
            return value
        if isinstance(value, (tuple, list)) and len(value) == 3:
            return Limit(*value)
        if isinstance(value, dict):
            return Limit(
                hits=int(value.get('hits', 10)),
                window=int(value.get('window', 300)),
                cooldown=int(value.get('cooldown', 300)),
            )
    return DEFAULT_LIMITS.get(scope, Limit(hits=30, window=300, cooldown=300))


def _hash_key(key):
    return hashlib.sha256(str(key).encode('utf-8')).hexdigest()[:64]


def _block_cache_key(scope, key_hash):
    return f'cc:block:{scope}:{key_hash}'


def _window_start(window_seconds):
    """Align the window to a fixed boundary so all requests share one row."""
    now = timezone.now()
    epoch = int(now.timestamp())
    start = epoch - (epoch % max(1, int(window_seconds)))
    return datetime.fromtimestamp(start, tz=dt_timezone.utc)


def _record_event(scope, request, key_hash, retry_after):
    """Persist the block for monitoring. Never breaks the request."""
    from .models import ThrottleEvent, hash_value

    try:
        from .traffic import get_client_ip

        ip_hash = hash_value(get_client_ip(request)) if request is not None else ''
        ThrottleEvent.objects.create(
            scope=scope,
            action='throttled',
            path=(request.path[:255] if request is not None else ''),
            ip_hash=ip_hash,
            key_hash=key_hash[:64],
            user_id=getattr(getattr(request, 'user', None), 'pk', None),
            retry_after=retry_after,
        )
    except Exception:  # pragma: no cover - monitoring must never break requests
        logger.exception('Failed to record throttle event for scope=%s', scope)


def check_rate_limit(scope, key, request=None, cost=1, record=True):
    """
    Count one hit for (scope, key) and report whether it is allowed.

    `key` should be the narrowest identity that still protects the resource
    (an IP, an IP+username pair, a user id, ...).
    """
    from .models import ThrottleCounter

    limit = get_limit(scope)
    key_hash = _hash_key(key)
    cache_key = _block_cache_key(scope, key_hash)

    blocked_until = cache.get(cache_key)
    if blocked_until:
        return ThrottleResult(
            allowed=False, scope=scope, retry_after=limit.retry_after,
            hits=limit.hits + 1, limit=limit.hits,
        )

    window_start = _window_start(limit.window)
    hits = 0
    try:
        updated = ThrottleCounter.objects.filter(
            scope=scope, key_hash=key_hash, window_start=window_start
        ).update(hits=F('hits') + cost, updated_at=timezone.now())
        if updated:
            hits = (
                ThrottleCounter.objects.filter(
                    scope=scope, key_hash=key_hash, window_start=window_start
                ).values_list('hits', flat=True).first()
                or 0
            )
        else:
            try:
                with transaction.atomic():
                    ThrottleCounter.objects.create(
                        scope=scope, key_hash=key_hash,
                        window_start=window_start, hits=cost,
                    )
                hits = cost
            except IntegrityError:
                # Another worker created the row first.
                ThrottleCounter.objects.filter(
                    scope=scope, key_hash=key_hash, window_start=window_start
                ).update(hits=F('hits') + cost, updated_at=timezone.now())
                hits = (
                    ThrottleCounter.objects.filter(
                        scope=scope, key_hash=key_hash, window_start=window_start
                    ).values_list('hits', flat=True).first()
                    or 0
                )
    except Exception:
        # A broken counter must not take the site down: fail open but visible.
        logger.exception('Rate limit counter failed for scope=%s', scope)
        return ThrottleResult(allowed=True, scope=scope, hits=0, limit=limit.hits)

    if hits > limit.hits:
        cache.set(cache_key, True, limit.retry_after)
        if record:
            _record_event(scope, request, key_hash, limit.retry_after)
        return ThrottleResult(
            allowed=False, scope=scope, retry_after=limit.retry_after,
            hits=hits, limit=limit.hits,
        )

    return ThrottleResult(allowed=True, scope=scope, hits=hits, limit=limit.hits)


def is_blocked(scope, key):
    """Cheap check (cache only) — used where counting is not wanted."""
    limit = get_limit(scope)
    return bool(cache.get(_block_cache_key(scope, _hash_key(key)))), limit.retry_after


def reset(scope, key):
    """Clear a block (used by tests and by admins after a false positive)."""
    from .models import ThrottleCounter

    key_hash = _hash_key(key)
    cache.delete(_block_cache_key(scope, key_hash))
    ThrottleCounter.objects.filter(scope=scope, key_hash=key_hash).delete()
