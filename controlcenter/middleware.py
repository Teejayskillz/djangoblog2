import hashlib
import re
from urllib.parse import urlparse
from django.utils import timezone
from .models import PageVisit

# Path prefixes to ignore in public traffic analytics
IGNORED_PATH_PREFIXES = (
    '/admin/',
    '/control-center/',
    '/static/',
    '/media/',
    '/favicon.ico',
    '/robots.txt',
    '/sitemap.xml',
    '/.well-known/',
)

# Common bot indicators
BOT_REGEX = re.compile(
    r'(googlebot|bingbot|slurp|duckduckbot|baiduspider|yandexbot|sogou|exabot|facebot|facebookexternalhit|ia_archiver|mj12bot|semrushbot|ahrefsbot|dotbot|rogerbot|twitterbot|linkedinbot|pinterest|whatsapp|applebot)',
    re.IGNORECASE
)

# Mobile/Tablet indicators
MOBILE_REGEX = re.compile(r'(android|iphone|ipod|blackberry|windows phone|mobile)', re.IGNORECASE)
TABLET_REGEX = re.compile(r'(ipad|tablet|playbook|silk)', re.IGNORECASE)


def get_client_ip(request):
    """Retrieve client IP respecting common reverse proxy / CloudLinux headers."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('HTTP_CF_CONNECTING_IP') or request.META.get('REMOTE_ADDR') or ''
    return ip


def get_device_type(user_agent):
    if not user_agent:
        return 'other'
    if BOT_REGEX.search(user_agent):
        return 'bot'
    if TABLET_REGEX.search(user_agent):
        return 'tablet'
    if MOBILE_REGEX.search(user_agent):
        return 'mobile'
    return 'desktop'


def get_anonymized_visitor_id(ip, user_agent, date_str):
    """Generate a daily anonymized 32-character hash for visitor uniqueness."""
    raw = f"{ip}|{user_agent}|{date_str}|nzd-salt-2026"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


class TrafficAnalyticsMiddleware:
    """
    Middleware that asynchronously or safely records genuine public visits.
    Only tracks GET requests to public site routes with status < 400.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            self.process_visit(request, response)
        except Exception:
            # Never let analytics tracking crash a live request
            pass

        return response

    def process_visit(self, request, response):
        # Only track GET/HEAD
        if request.method not in ('GET', 'HEAD'):
            return

        # Only track successful responses (skip 404s, 500s, redirects or track only 200)
        if response.status_code >= 400:
            return

        path = request.path

        # Ignore backend, control center, static, media, crawler files
        for prefix in IGNORED_PATH_PREFIXES:
            if path.startswith(prefix) or path == prefix:
                return

        user_agent = request.META.get('HTTP_USER_AGENT', '')[:300]
        device = get_device_type(user_agent)

        # Ignore bot hits from primary metrics
        if device == 'bot':
            return

        # Check authentication / staff
        user = getattr(request, 'user', None)
        is_auth = user.is_authenticated if user else False
        is_staff = user.is_staff if (user and is_auth) else False

        # Exclude internal staff browsing the frontend if desired, or record flag
        # We record staff flag so admin can filter out internal clicks in analytics
        today_str = timezone.now().strftime('%Y-%m-%d')
        ip = get_client_ip(request)
        visitor_id = get_anonymized_visitor_id(ip, user_agent, today_str)

        session_key = ''
        if hasattr(request, 'session') and request.session.session_key:
            session_key = request.session.session_key[:40]

        referrer = request.META.get('HTTP_REFERER', '')[:500]
        referrer_domain = ''
        if referrer:
            try:
                parsed = urlparse(referrer)
                referrer_domain = (parsed.netloc or '').lower()
                # If referrer is this site itself, keep referrer_domain empty or internal
                host = (request.get_host() or '').lower()
                if referrer_domain == host or referrer_domain.endswith('.' + host):
                    referrer_domain = 'internal'
            except Exception:
                referrer_domain = ''

        PageVisit.objects.create(
            path=path[:500],
            method=request.method,
            status_code=response.status_code,
            visitor_id=visitor_id,
            session_key=session_key or None,
            referrer=referrer,
            referrer_domain=referrer_domain[:150],
            device_type=device,
            user_agent=user_agent,
            is_authenticated=is_auth,
            is_staff_user=is_staff,
            timestamp=timezone.now(),
        )
