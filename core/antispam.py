"""
Dependency-free anti-spam helpers used by public forms.

Layers (all optional/configurable, none require Cloudflare):
* signed form token -> rejects submissions that are too fast to be human and
  expired/replayed tokens;
* honeypot field (implemented per form);
* link counting -> obvious link spam is held for moderation;
* optional Cloudflare Turnstile verification when keys are configured (the core
  protections keep working when it is not).
"""

import json
import logging
import re
import urllib.parse
import urllib.request

from django.conf import settings
from django.core import signing
from django.utils import timezone

logger = logging.getLogger(__name__)

FORM_TOKEN_SALT = 'nzd.formtoken'
FORM_TOKEN_MAX_AGE = 60 * 60 * 2

_URL_RE = re.compile(r'(https?://|www\.)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Signed form token: minimum submission time + replay window
# ---------------------------------------------------------------------------
def make_form_token():
    return signing.dumps({'t': int(timezone.now().timestamp())}, salt=FORM_TOKEN_SALT)


def validate_form_token(token, min_seconds=None, max_age=FORM_TOKEN_MAX_AGE):
    """
    Validate a submitted token.

    Returns (ok, reason). `reason` is safe to log and is never shown verbatim to
    visitors, so it cannot be used to tune an attack.
    """
    if min_seconds is None:
        min_seconds = float(getattr(settings, 'FORM_MIN_SUBMIT_SECONDS', 3))
    if not token:
        return False, 'missing'
    try:
        payload = signing.loads(token, salt=FORM_TOKEN_SALT, max_age=max_age)
    except signing.SignatureExpired:
        return False, 'expired'
    except signing.BadSignature:
        return False, 'invalid'
    except Exception:
        return False, 'invalid'

    issued = payload.get('t') if isinstance(payload, dict) else None
    if not isinstance(issued, (int, float)):
        return False, 'invalid'
    age = timezone.now().timestamp() - issued
    if age < min_seconds:
        return False, 'too_fast'
    if age > max_age:
        return False, 'expired'
    return True, ''


def count_links(text):
    return len(_URL_RE.findall(text or ''))


def looks_like_link_spam(text):
    limit = int(getattr(settings, 'SPAM_MAX_LINKS', 2))
    return count_links(text) > limit


# ---------------------------------------------------------------------------
# Optional Turnstile (disabled unless both keys are configured)
# ---------------------------------------------------------------------------
def turnstile_configured():
    return bool(
        getattr(settings, 'TURNSTILE_SITE_KEY', '')
        and getattr(settings, 'TURNSTILE_SECRET_KEY', '')
    )


def turnstile_widget():
    """Template context for the Turnstile widget (empty when not configured)."""
    if not turnstile_configured():
        return {'configured': False}
    return {
        'configured': True,
        'site_key': settings.TURNSTILE_SITE_KEY,
        'field_name': 'cf-turnstile-response',
    }


def verify_turnstile(request):
    """
    Verify the Turnstile response, if Turnstile is configured.

    Returns (ok, reason). When the service is unreachable the result follows
    TURNSTILE_FAIL_OPEN (default True) so a third-party outage cannot stop
    legitimate comments.
    """
    if not turnstile_configured():
        return True, 'disabled'

    token = (request.POST.get('cf-turnstile-response') or '').strip()
    if not token:
        return False, 'missing'

    data = urllib.parse.urlencode({
        'secret': settings.TURNSTILE_SECRET_KEY,
        'response': token,
    }).encode('utf-8')

    try:
        with urllib.request.urlopen(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data=data,
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode('utf-8'))
        if payload.get('success'):
            return True, 'ok'
        logger.info('Turnstile rejected a submission: %s', payload.get('error-codes'))
        return False, 'failed'
    except Exception as exc:
        logger.warning('Turnstile verification error: %s', exc)
        fail_open = bool(getattr(settings, 'TURNSTILE_FAIL_OPEN', True))
        return fail_open, 'error'
