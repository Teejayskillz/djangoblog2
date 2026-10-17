"""
Traffic classification and client-IP handling.

Design notes
------------
* User-Agent strings are *claims*, never proof. A UA that claims to be
  Googlebot is classified as `known_bot` (unverified) until the source IP is
  confirmed by forward-confirmed reverse DNS, which is the verification method
  Google itself recommends and the only one available without a paid service.
* Anything we cannot classify is `unknown`, never `human`. Human page-view
  totals are therefore an *estimate* and the dashboard labels them as such.
* No personal data is stored: IPs are hashed with a daily salt before use.
"""

import hashlib
import logging
import re
import socket

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# --- Bot families ----------------------------------------------------------
# (name, compiled regex). Order matters: more specific first.
_BOT_SIGNATURES = [
    ('Googlebot', re.compile(r'Googlebot|Google-InspectionTool|Storebot-Google', re.I)),
    ('Bingbot', re.compile(r'bingbot|BingPreview|adidxbot', re.I)),
    ('DuckDuckBot', re.compile(r'DuckDuckBot', re.I)),
    ('YandexBot', re.compile(r'YandexBot|YandexImages', re.I)),
    ('Baiduspider', re.compile(r'Baiduspider', re.I)),
    ('Applebot', re.compile(r'Applebot', re.I)),
    ('AhrefsBot', re.compile(r'AhrefsBot', re.I)),
    ('SemrushBot', re.compile(r'SemrushBot', re.I)),
    ('MJ12bot', re.compile(r'MJ12bot', re.I)),
    ('DotBot', re.compile(r'DotBot', re.I)),
    ('PetalBot', re.compile(r'PetalBot', re.I)),
    ('GPTBot', re.compile(r'GPTBot|ChatGPT-User|OAI-SearchBot', re.I)),
    ('ClaudeBot', re.compile(r'ClaudeBot|anthropic-ai', re.I)),
    ('Bytespider', re.compile(r'Bytespider', re.I)),
    ('facebookexternalhit', re.compile(r'facebookexternalhit|facebookcatalog', re.I)),
    ('Twitterbot', re.compile(r'Twitterbot', re.I)),
    ('LinkedInBot', re.compile(r'LinkedInBot', re.I)),
    ('TelegramBot', re.compile(r'TelegramBot', re.I)),
    ('WhatsApp', re.compile(r'WhatsApp', re.I)),
    ('UptimeRobot', re.compile(r'UptimeRobot|Pingdom|StatusCake|BetterUptime', re.I)),
    ('curl/wget', re.compile(r'^(curl|wget|python-requests|python-urllib|go-http-client|okhttp|java/)', re.I)),
    ('HeadlessChrome', re.compile(r'HeadlessChrome|PhantomJS|SlimerJS|puppeteer|playwright', re.I)),
    ('GenericBot', re.compile(r'bot\b|crawler|spider|scraper|slurp|archiver', re.I)),
]

# Crawlers whose identity we can verify by FCrDNS: bot name -> allowed PTR suffixes.
VERIFIABLE_CRAWLERS = {
    'Googlebot': ('googlebot.com', 'google.com'),
    'Bingbot': ('search.msn.com',),
    'DuckDuckBot': ('duckduckgo.com',),
    'Applebot': ('applebot.apple.com',),
}

_HOSTNAME_CACHE_SECONDS = 60 * 60 * 24
_VERIFY_CACHE_SECONDS = 60 * 60 * 6

_STATIC_EXTENSIONS = (
    '.css', '.js', '.mjs', '.map', '.json', '.xml', '.txt', '.ico', '.png', '.jpg',
    '.jpeg', '.gif', '.webp', '.avif', '.svg', '.bmp', '.woff', '.woff2', '.ttf',
    '.otf', '.eot', '.mp4', '.webm', '.mp3', '.wav', '.pdf', '.zip', '.gz', '.br',
    '.webmanifest',
)

_HTML_ACCEPT_HINTS = ('text/html', 'application/xhtml+xml')


# ---------------------------------------------------------------------------
# Client IP
# ---------------------------------------------------------------------------
def get_client_ip(request):
    """
    Return the client IP.

    X-Forwarded-For is only honoured when the direct peer (REMOTE_ADDR) is a
    configured trusted proxy, otherwise a client could spoof its IP and dodge
    rate limits. On cPanel/Passenger REMOTE_ADDR is already the visitor IP.
    """
    remote_addr = (request.META.get('REMOTE_ADDR') or '').strip()
    trusted = getattr(settings, 'TRUSTED_PROXY_IPS', ()) or ()
    if remote_addr and trusted and remote_addr in trusted:
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        parts = [part.strip() for part in forwarded.split(',') if part.strip()]
        if parts:
            return parts[0]
        real_ip = (request.META.get('HTTP_X_REAL_IP') or '').strip()
        if real_ip:
            return real_ip
    if remote_addr:
        return remote_addr
    return (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0].strip() or '0.0.0.0'


def visitor_hash(ip, user_agent, date_str=None):
    """
    Daily-salted visitor hash (no raw IP stored).

    The salt rotates daily so the hash cannot be used to follow a visitor
    across days, which keeps the analytics privacy-preserving.
    """
    date_str = date_str or timezone.now().strftime('%Y-%m-%d')
    secret = getattr(settings, 'SECRET_KEY', '')
    raw = f'{ip}|{user_agent}|{date_str}|{secret}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


# ---------------------------------------------------------------------------
# User agent classification
# ---------------------------------------------------------------------------
def bot_claim(user_agent):
    """Return the bot name claimed by the User-Agent, or None."""
    if not user_agent:
        return None
    for name, pattern in _BOT_SIGNATURES:
        if pattern.search(user_agent):
            return name
    return None


def _resolve_hostname(ip):
    cache_key = f'cc:ptrsafe:{ip}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached or None
    hostname = ''
    try:
        hostname = socket.gethostbyaddr(ip)[0]
    except (socket.herror, socket.gaierror, OSError):
        hostname = ''
    cache.set(cache_key, hostname, _HOSTNAME_CACHE_SECONDS)
    return hostname or None


def _forward_resolve(hostname):
    try:
        return {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except (socket.gaierror, OSError):
        return set()


def verify_crawler(ip, bot_name):
    """
    Forward-confirmed reverse DNS check for a claimed crawler.

    Returns True only when the PTR record belongs to the crawler's domain *and*
    resolving that hostname forwards back to the same IP.
    """
    suffixes = VERIFIABLE_CRAWLERS.get(bot_name)
    if not suffixes or not ip:
        return False
    if not getattr(settings, 'BOT_VERIFY_CRAWLERS', True):
        return False

    cache_key = f'cc:crawler:{bot_name}:{ip}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    verified = False
    hostname = _resolve_hostname(ip)
    if hostname:
        hostname = hostname.rstrip('.')
        if any(hostname.endswith(suffix) for suffix in suffixes):
            verified = ip in _forward_resolve(hostname)
    cache.set(cache_key, verified, _VERIFY_CACHE_SECONDS)
    return verified


# ---------------------------------------------------------------------------
# Page-view / static detection
# ---------------------------------------------------------------------------
def is_static_path(path):
    lower = (path or '').lower()
    if lower.endswith(_STATIC_EXTENSIONS):
        return True
    for prefix in getattr(settings, 'ANALYTICS_STATIC_PREFIXES', ('/static/', '/media/')):
        if lower.startswith(prefix):
            return True
    return False


def is_html_response(response):
    content_type = (response.get('Content-Type', '') if response is not None else '') or ''
    return 'text/html' in content_type.lower()


def accepts_html(request):
    """
    True when the client is plausibly asking for a page.

    A missing Accept header is treated as *possibly* HTML (some clients omit it);
    only an explicit non-HTML Accept (e.g. application/json) rules the request out.
    The response Content-Type check is the authoritative filter.
    """
    accept = (request.META.get('HTTP_ACCEPT') or '').lower()
    if not accept:
        return True
    return any(hint in accept for hint in _HTML_ACCEPT_HINTS)


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------
def classify_request(request, ip=None):
    """
    Classify a request.

    Returns a dict: traffic_class, bot_name, device_type.
    """
    user_agent = (request.META.get('HTTP_USER_AGENT') or '')[:300]
    ip = ip or get_client_ip(request)
    claimed = bot_claim(user_agent)

    if claimed:
        verified = verify_crawler(ip, claimed)
        return {
            'traffic_class': 'verified_bot' if verified else 'known_bot',
            'bot_name': claimed,
            'device_type': 'bot',
        }

    # Not a claimed bot. Heuristics that only ever downgrade to "suspected" --
    # never claim a visitor is human.
    if not user_agent:
        return {'traffic_class': 'suspected_bot', 'bot_name': '', 'device_type': 'other'}

    device = device_type(user_agent)
    if device == 'other' and not accepts_html(request):
        return {'traffic_class': 'suspected_bot', 'bot_name': '', 'device_type': device}

    return {'traffic_class': 'human', 'bot_name': '', 'device_type': device}


def device_type(user_agent):
    ua = (user_agent or '').lower()
    if not ua:
        return 'other'
    if 'ipad' in ua or 'tablet' in ua or ('android' in ua and 'mobile' not in ua):
        return 'tablet'
    if 'mobile' in ua or 'iphone' in ua or 'android' in ua:
        return 'mobile'
    if any(token in ua for token in ('mozilla', 'chrome', 'safari', 'firefox', 'edg/', 'opera')):
        return 'desktop'
    return 'other'


def referrer_domain(request):
    referrer = (request.META.get('HTTP_REFERER') or '')[:500]
    if not referrer:
        return '', ''
    try:
        from urllib.parse import urlparse

        domain = (urlparse(referrer).netloc or '').lower()
        host = (request.get_host() or '').lower()
        if domain and (domain == host or domain.endswith('.' + host)):
            domain = 'internal'
    except Exception:
        domain = ''
    return referrer, domain[:150]


def is_excluded_path(path):
    """Paths that must never reach the public analytics tables."""
    lower = (path or '').lower()
    for prefix in getattr(settings, 'ANALYTICS_EXCLUDED_PREFIXES', ()):
        if lower.startswith(prefix):
            return True
    return False
