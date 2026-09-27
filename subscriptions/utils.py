from django.core.signing import TimestampSigner
from django.conf import settings
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


def user_has_active_subscription(user):
    """
    Returns True if the user is authenticated and has an active, non-expired subscription.
    """
    if not user or not user.is_authenticated:
        return False

    # Superusers / staff can optionally be treated as having access if desired,
    # but let's check for explicit subscription or superuser
    if user.is_superuser:
        return True

    subscription = getattr(user, 'subscription', None)
    if subscription and subscription.is_valid():
        return True

    return False


def generate_cdn_vip_token(user_id):
    """
    Generates a signed, timestamped authentication token for VIP access to the CDN shortener site.
    Signs the user_id using Django's TimestampSigner with CDN_SHARED_SECRET.
    """
    secret = getattr(settings, 'CDN_SHARED_SECRET', getattr(settings, 'SECRET_KEY', ''))
    signer = TimestampSigner(key=secret)
    return signer.sign(str(user_id))


def append_vip_token_to_url(url, token):
    """
    Safely appends the signed VIP authentication token to a CDN download URL query string.
    """
    if not url or not token:
        return url
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    query_params['token'] = [token]
    new_query = urlencode(query_params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))

