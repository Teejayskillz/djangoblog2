"""
Service layer for the optional **External Link Generator** integration.

Everything security-relevant lives here so the HTTP views stay thin:

* API key issue / rotate / revoke (only a SHA-256 digest is stored).
* Signed server-to-server request authentication (Bearer key + HMAC-SHA256 over
  timestamp, single-use nonce and the exact request body), clock-skew window,
  replay rejection and domain/installation binding.
* Opaque session tokens (only a digest is stored), expiry, server-side
  ``not_before`` and atomic single-use redemption.
* Handoff URL construction and event logging that never records tokens, keys or
  download destinations.

The module deliberately does not import ``core.views`` (no circular imports) and
reads the existing download/VIP helpers from ``subscriptions.utils`` at call
time, so the integration reuses the real download logic instead of duplicating
it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import quote, urlsplit, urlunsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from .external_link_models import (
    ExternalLinkConfig,
    ExternalLinkEvent,
    ExternalLinkNonce,
    ExternalLinkSession,
    _host_of,
)

logger = logging.getLogger("core.external_links")

API_KEY_PREFIX = "nzd_live_"
API_KEY_BYTES = 32
SESSION_TOKEN_BYTES = 32
SESSION_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,96}$")
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ENTRY_TEMPLATE_TOKEN = "{token}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class ApiFailure(Exception):
    """A controlled API failure that becomes a JSON error response."""

    def __init__(self, code, message, status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.extra = extra

    def as_payload(self):
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, **self.extra},
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def get_config():
    return ExternalLinkConfig.load()


def hash_api_key(raw_key):
    """One-way digest for an API key (the plaintext is never stored)."""
    return hashlib.sha256(f"nzdworld-el-key:{raw_key or ''}".encode("utf-8")).hexdigest()


def hash_session_token(raw_token):
    return hashlib.sha256(f"nzdworld-el-token:{raw_token or ''}".encode("utf-8")).hexdigest()


def generate_api_key():
    return API_KEY_PREFIX + secrets.token_urlsafe(API_KEY_BYTES)


def verify_api_key(config, presented_key):
    if not presented_key or not config.has_active_key:
        return False
    return constant_time_compare(hash_api_key(presented_key), config.api_key_hash)


def normalize_site_url(raw_url):
    """
    Canonicalise an installation URL to ``https://host[:port][/path]``.

    Returns ``""`` when the value is not an absolute, credential-free HTTPS URL
    (the API only ever talks to HTTPS sites, so plain HTTP is refused).
    """
    raw = (raw_url or "").strip()
    if not raw or len(raw) > 300:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ""
    if parts.username or parts.password or parts.query or parts.fragment:
        return ""
    host = parts.hostname.lower().strip(".")
    netloc = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), "", ""))


def site_origin(url):
    """``https://host[:port]`` part of a URL (with any sub-directory removed)."""
    normalized = normalize_site_url(url)
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def hosts_match(first, second):
    host_a, host_b = _host_of(first), _host_of(second)
    return bool(host_a) and host_a == host_b


def validate_entry_template(template, site_url):
    """
    Validate the WordPress-reported handoff URL template.

    Must be absolute HTTPS, contain exactly one ``{token}`` placeholder, carry no
    fragment/userinfo and live on the registered site's host. Returns the
    cleaned template or ``""``.
    """
    candidate = (template or "").strip()
    if not candidate or len(candidate) > 500:
        return ""
    if any(char in candidate for char in ("\r", "\n", "\t", " ")):
        return ""
    if candidate.count(ENTRY_TEMPLATE_TOKEN) != 1:
        return ""
    leftover = candidate.replace(ENTRY_TEMPLATE_TOKEN, "")
    if "{" in leftover or "}" in leftover:
        return ""
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ""
    if parts.username or parts.password or parts.fragment:
        return ""
    if not hosts_match(site_url, f"https://{parts.netloc}"):
        return ""
    return candidate


def is_https_request(request):
    """Whether the incoming API call arrived over TLS (proxy-aware)."""
    if not getattr(settings, "EXTERNAL_LINK_REQUIRE_HTTPS", True):
        return True
    if request.is_secure():
        return True
    trusted = tuple(getattr(settings, "TRUSTED_PROXY_IPS", ()) or ())
    peer = request.META.get("REMOTE_ADDR", "")
    if peer and peer in trusted:
        forwarded = request.META.get("HTTP_X_FORWARDED_PROTO", "").split(",")[0].strip().lower()
        if forwarded == "https":
            return True
    return False


def client_ip_hash(request):
    """Salted hash of the visitor IP — never the raw address."""
    ip = ""
    try:
        from controlcenter.traffic import get_client_ip

        ip = get_client_ip(request) or ""
    except Exception:  # pragma: no cover - defensive
        ip = request.META.get("REMOTE_ADDR", "") or ""
    if not ip:
        return ""
    salt = getattr(settings, "SECRET_KEY", "") or ""
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()[:64]


def iso(moment):
    return moment.isoformat().replace("+00:00", "Z") if moment else ""


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------
def log_event(event, *, level=ExternalLinkEvent.Level.INFO, message="", session=None,
              api_key_prefix="", ip_hash=""):
    """Persist an audit event. Never pass secrets or download URLs here."""
    safe_message = (message or "")[:300]
    try:
        ExternalLinkEvent.objects.create(
            event=event,
            level=level,
            message=safe_message,
            session=session,
            api_key_prefix=(api_key_prefix or "")[:24],
            ip_hash=(ip_hash or "")[:64],
        )
    except Exception:  # pragma: no cover - logging must never break a download
        logger.exception("Could not persist external link event %s", event)

    log = logger.warning if level in ("warning", "error") else logger.info
    log("external_link event=%s %s", event, safe_message)


# ---------------------------------------------------------------------------
# API key lifecycle
# ---------------------------------------------------------------------------
def issue_api_key(config=None):
    """Create a new key; returns the plaintext (to be shown exactly once)."""
    config = config or get_config()
    raw_key = generate_api_key()
    config.api_key_hash = hash_api_key(raw_key)
    config.api_key_prefix = raw_key[:16]
    config.api_key_created_at = timezone.now()
    config.api_key_revoked_at = None
    config.save(
        update_fields=[
            "api_key_hash",
            "api_key_prefix",
            "api_key_created_at",
            "api_key_revoked_at",
            "updated_at",
        ]
    )
    log_event(
        "api_key_generated",
        message=f"New API key issued ({config.api_key_prefix}…).",
        api_key_prefix=config.api_key_prefix,
    )
    return raw_key


def revoke_api_key(config=None):
    config = config or get_config()
    if not config.api_key_hash:
        return False
    config.api_key_revoked_at = timezone.now()
    config.save(update_fields=["api_key_revoked_at", "updated_at"])
    log_event(
        "api_key_revoked",
        level=ExternalLinkEvent.Level.WARNING,
        message="API key revoked; WordPress calls will be rejected.",
        api_key_prefix=config.api_key_prefix,
    )
    return True


def reset_registered_domain(config=None, *, keep_key=True):
    """Forget the connected WordPress site (optionally revoking its key too)."""
    config = config or get_config()
    fields = ["wordpress_site_url", "wordpress_entry_template", "updated_at"]
    config.wordpress_site_url = ""
    config.wordpress_entry_template = ""
    if not keep_key:
        config.api_key_revoked_at = timezone.now()
        fields.append("api_key_revoked_at")
    config.save(update_fields=fields)
    log_event(
        "domain_reset",
        level=ExternalLinkEvent.Level.WARNING,
        message="Registered WordPress site cleared; the next Test Connection re-registers it.",
        api_key_prefix=config.api_key_prefix,
    )
    return True


# ---------------------------------------------------------------------------
# Signed request authentication
# ---------------------------------------------------------------------------
@dataclass
class ApiContext:
    config: ExternalLinkConfig
    request: object
    site_url: str
    body: bytes
    data: dict = field(default_factory=dict)
    plugin_version: str = ""
    ip_hash: str = ""

    @property
    def site_host(self):
        return _host_of(self.site_url)

    @property
    def key_prefix(self):
        return self.config.api_key_prefix


def expected_signature(secret, timestamp, nonce, raw_body):
    """HMAC-SHA256 over ``timestamp.nonce.sha256(body)``."""
    body_digest = hashlib.sha256(raw_body or b"").hexdigest()
    payload = f"{timestamp}.{nonce}.{body_digest}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _record_nonce(nonce, *, skew):
    """Reject replayed requests; prune old nonces opportunistically."""
    try:
        ExternalLinkNonce.objects.create(nonce=nonce)
    except IntegrityError:
        return False
    if secrets.randbelow(40) == 0:
        cutoff = timezone.now() - timedelta(seconds=max(600, int(skew) * 3))
        try:
            ExternalLinkNonce.objects.filter(created_at__lt=cutoff).delete()
        except Exception:  # pragma: no cover
            logger.exception("Could not prune external link nonces")
    return True


def authenticate_api_request(request, *, scope="external_api", allow_when_disabled=False):
    """
    Authenticate a WordPress -> NZDWorld API call.

    Returns ``(ApiContext, None)`` on success or ``(None, JsonResponse)`` with a
    controlled error. The response never contains credentials.

    ``allow_when_disabled`` is only used by the connection test: an operator
    must be able to register the installation (so the admin screen can report
    status) while the generator itself stays switched off.
    """
    if not is_https_request(request):
        log_event(
            "auth_failed",
            level=ExternalLinkEvent.Level.WARNING,
            message="API call rejected: plaintext HTTP.",
            ip_hash=client_ip_hash(request),
        )
        return None, error_response(
            ApiFailure("insecure_transport", "HTTPS is required for the integration API.", 400)
        )

    config = get_config()

    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        return None, error_response(
            ApiFailure("unauthorized", "A Bearer API key is required.", 401)
        )
    presented_key = authorization[7:].strip()
    if len(presented_key) < 20 or len(presented_key) > 200:
        return None, error_response(ApiFailure("unauthorized", "Invalid API key.", 401))

    if not verify_api_key(config, presented_key):
        log_event(
            "auth_failed",
            level=ExternalLinkEvent.Level.WARNING,
            message="API call rejected: bad or revoked API key.",
            ip_hash=client_ip_hash(request),
        )
        return None, error_response(
            ApiFailure("unauthorized", "Invalid or revoked API key.", 401)
        )

    # Only authenticated callers learn whether the feature is switched on.
    if not config.enabled and not allow_when_disabled:
        return None, error_response(
            ApiFailure(
                "integration_disabled",
                "The External Link Generator is disabled on NZDWorld.",
                403,
            )
        )

    timestamp = (request.headers.get("X-NZD-Timestamp") or "").strip()
    nonce = (request.headers.get("X-NZD-Nonce") or "").strip()
    signature = (request.headers.get("X-NZD-Signature") or "").strip().lower()
    if not timestamp.isdigit() or not NONCE_RE.match(nonce or "") or not signature:
        return None, error_response(
            ApiFailure(
                "invalid_signature",
                "Signed request headers (X-NZD-Timestamp, X-NZD-Nonce, X-NZD-Signature) are required.",
                401,
            )
        )

    skew = max(30, int(config.api_clock_skew_seconds or 300))
    now_ts = int(timezone.now().timestamp())
    if abs(now_ts - int(timestamp)) > skew:
        log_event(
            "auth_failed",
            level=ExternalLinkEvent.Level.WARNING,
            message="API call rejected: timestamp outside the allowed window.",
            api_key_prefix=config.api_key_prefix,
            ip_hash=client_ip_hash(request),
        )
        return None, error_response(
            ApiFailure("stale_request", "The request timestamp is outside the allowed window.", 401)
        )

    raw_body = request.body or b""
    expected = expected_signature(presented_key, timestamp, nonce, raw_body)
    if not constant_time_compare(signature, expected):
        log_event(
            "auth_failed",
            level=ExternalLinkEvent.Level.WARNING,
            message="API call rejected: signature mismatch.",
            api_key_prefix=config.api_key_prefix,
            ip_hash=client_ip_hash(request),
        )
        return None, error_response(
            ApiFailure("invalid_signature", "The request signature is invalid.", 401)
        )

    if not _record_nonce(nonce, skew=skew):
        log_event(
            "auth_failed",
            level=ExternalLinkEvent.Level.WARNING,
            message="API call rejected: replayed nonce.",
            api_key_prefix=config.api_key_prefix,
            ip_hash=client_ip_hash(request),
        )
        return None, error_response(
            ApiFailure("replayed_request", "This request has already been processed.", 401)
        )

    try:
        data = json.loads((raw_body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, error_response(ApiFailure("invalid_json", "The request body is not valid JSON.", 400))
    if not isinstance(data, dict):
        return None, error_response(ApiFailure("invalid_json", "The request body must be a JSON object.", 400))

    site_url = normalize_site_url(data.get("site_url") or "")
    if not site_url:
        return None, error_response(
            ApiFailure("invalid_site", "site_url must be an absolute HTTPS URL.", 400)
        )

    if (
        config.domain_locked
        and config.site_host
        and not hosts_match(site_url, config.wordpress_site_url)
    ):
        log_event(
            "domain_mismatch",
            level=ExternalLinkEvent.Level.ERROR,
            message=f"Call from {_host_of(site_url)} rejected; registered host is {config.site_host}.",
            api_key_prefix=config.api_key_prefix,
            ip_hash=client_ip_hash(request),
        )
        return None, error_response(
            ApiFailure(
                "domain_mismatch",
                "This WordPress installation is not the registered one.",
                403,
            )
        )

    limit = _check_api_rate_limit(config, request, scope=scope)
    if limit is not None:
        return None, limit

    # Cheap, useful bookkeeping: the key is in active use.
    try:
        ExternalLinkConfig.objects.filter(pk=config.pk).update(api_key_last_used_at=timezone.now())
    except Exception:  # pragma: no cover
        logger.exception("Could not update api_key_last_used_at")

    return (
        ApiContext(
            config=config,
            request=request,
            site_url=site_url,
            body=raw_body,
            data=data,
            plugin_version=str(data.get("plugin_version") or "")[:32],
            ip_hash=client_ip_hash(request),
        ),
        None,
    )


def _check_api_rate_limit(config, request, *, scope):
    from controlcenter.throttle import check_rate_limit

    key = f"{config.api_key_prefix}:{client_ip_hash(request)}"
    try:
        limit = check_rate_limit(scope, key, request)
    except Exception:  # pragma: no cover - never break the API on counter issues
        logger.exception("External link rate limiter failed")
        return None
    if limit.allowed:
        return None
    log_event(
        "rate_limited",
        level=ExternalLinkEvent.Level.WARNING,
        message=f"API rate limit hit for scope {scope}.",
        api_key_prefix=config.api_key_prefix,
        ip_hash=client_ip_hash(request),
    )
    return error_response(
        ApiFailure(
            "rate_limited",
            "Too many requests. Please retry shortly.",
            429,
            retry_after=limit.retry_after,
        ),
        retry_after=limit.retry_after,
    )


def error_response(failure, *, retry_after=None):
    response = JsonResponse(failure.as_payload(), status=failure.status)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    if retry_after:
        response["Retry-After"] = str(int(retry_after))
    return response


# ---------------------------------------------------------------------------
# Connection registration / verification
# ---------------------------------------------------------------------------
def register_connection(ctx):
    """
    Record (or verify) the calling WordPress installation.

    First successful Test Connection from a fresh key registers the site and the
    entry URL template it reports (trust-on-first-use); later calls must come
    from the same host unless the domain lock is cleared by an administrator.
    """
    config = ctx.config
    data = ctx.data
    site_url = ctx.site_url
    entry_template = validate_entry_template(data.get("entry_url_template"), site_url)
    if not entry_template:
        raise ApiFailure(
            "invalid_entry_url",
            "entry_url_template must be an HTTPS URL on the site_url host containing {token}.",
            400,
        )

    if config.site_host and not hosts_match(site_url, config.wordpress_site_url):
        if config.domain_locked:
            raise ApiFailure(
                "domain_mismatch",
                "This WordPress installation is not the registered one.",
                403,
            )

    changed_fields = []
    if config.wordpress_site_url != site_url:
        config.wordpress_site_url = site_url
        changed_fields.append("wordpress_site_url")
    if config.wordpress_entry_template != entry_template:
        config.wordpress_entry_template = entry_template
        changed_fields.append("wordpress_entry_template")

    now = timezone.now()
    config.last_connection_at = now
    config.last_connection_ok = True
    config.last_connection_site_url = site_url
    config.last_connection_plugin_version = ctx.plugin_version
    config.last_connection_ip_hash = ctx.ip_hash
    config.last_connection_error = ""
    changed_fields += [
        "last_connection_at",
        "last_connection_ok",
        "last_connection_site_url",
        "last_connection_plugin_version",
        "last_connection_ip_hash",
        "last_connection_error",
        "updated_at",
    ]
    config.save(update_fields=changed_fields)

    log_event(
        "connection_verified",
        message=f"WordPress installation verified ({ctx.site_host}).",
        api_key_prefix=config.api_key_prefix,
        ip_hash=ctx.ip_hash,
    )

    return {
        "api_version": "1.0",
        "server_time": iso(now),
        "integration_enabled": config.enabled,
        "registered_site": config.wordpress_site_url,
        "entry_template_ok": True,
        "session_ttl_seconds": config.session_ttl_seconds,
        "min_countdown_seconds": config.min_countdown_seconds,
        "max_countdown_seconds": config.max_countdown_seconds,
        "message": (
            "Connection verified."
            if config.enabled
            else "Connection verified, but the generator is disabled on NZDWorld."
        ),
    }


# ---------------------------------------------------------------------------
# Generation sessions
# ---------------------------------------------------------------------------
def quality_label(quality):
    """Human label for admin lists — never contains the download URL."""
    if quality is None:
        return ""
    display = ""
    try:
        display = quality.get_quality_display()
    except Exception:  # pragma: no cover - defensive
        display = getattr(quality, "quality", "") or ""
    title = ""
    try:
        title = quality.post.title if quality.post_id else ""
    except Exception:  # pragma: no cover
        title = ""
    label = f"{display} — {title}" if title else display
    return label[:200]


def create_generation_session(quality, *, site_url, request=None, vip_user_id=None):
    """Create a new opaque session; returns ``(session, plaintext_token)``."""
    config = get_config()
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    now = timezone.now()
    session = ExternalLinkSession.objects.create(
        token_hash=hash_session_token(token),
        token_prefix=token[:10],
        quality=quality,
        quality_label=quality_label(quality),
        post_id=quality.post_id,
        post_title=(quality.post.title[:200] if quality.post_id else ""),
        site_url=site_url or "",
        vip_user_id=vip_user_id,
        expires_at=now + timedelta(seconds=int(config.session_ttl_seconds or 900)),
        created_ip_hash=client_ip_hash(request) if request is not None else "",
    )
    log_event(
        "session_created",
        message=f"Session {session.token_prefix}… created for quality #{quality.pk}.",
        session=session,
        api_key_prefix=config.api_key_prefix,
        ip_hash=session.created_ip_hash,
    )
    return session, token


def build_handoff_url(config, token):
    """Absolute WordPress entry URL for a token (raises ApiFailure if unusable)."""
    template = config.wordpress_entry_template or ""
    if template.count(ENTRY_TEMPLATE_TOKEN) != 1:
        raise ApiFailure("not_configured", "The WordPress entry URL is not configured.", 409)
    url = template.replace(ENTRY_TEMPLATE_TOKEN, quote(token, safe=""))
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ApiFailure("not_configured", "The WordPress entry URL is not valid.", 409)
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ApiFailure("not_configured", "The WordPress entry URL must be HTTPS.", 409)
    if config.site_host and parts.hostname.lower() != config.site_host:
        # Never redirect the browser to a different host than the registered one.
        raise ApiFailure(
            "not_configured", "The WordPress entry URL host does not match the registered site.", 409
        )
    return url


def start_handoff(request, quality, *, is_vip=False):
    """
    Begin the WordPress handoff for a real download-quality row.

    Returns an :class:`~django.http.HttpResponse` redirect when the integration
    takes over, or ``None`` in every other case so the caller keeps its original
    behaviour untouched (disabled, unconfigured, premium without VIP, error).
    """
    try:
        config = get_config()
    except Exception:  # pragma: no cover - e.g. migrations not applied yet
        logger.exception("External link config could not be loaded; using the direct download")
        return None

    if not config.enabled:
        return None
    if quality is None:
        return None
    if quality.is_premium and not is_vip:
        # Never bypass the existing premium gate: let the caller show its usual
        # "activate VIP" message.
        return None
    if not config.is_operational:
        _note_handoff_error(config, config.blocked_reason)
        return None

    # Rate limit the handoff per visitor. A broken counter must not block a
    # download, so a limiter error is treated as "allowed".
    from controlcenter.throttle import check_rate_limit

    try:
        limit = check_rate_limit("external_handoff", client_ip_hash(request) or "anon", request)
    except Exception:  # pragma: no cover
        logger.exception("External link handoff rate limiter failed")
        limit = None

    if limit is not None and not limit.allowed:
        from core.auth_views import rate_limited_response

        log_event(
            "handoff_rate_limited",
            level=ExternalLinkEvent.Level.WARNING,
            message="Handoff rate limit hit.",
            api_key_prefix=config.api_key_prefix,
            ip_hash=client_ip_hash(request),
        )
        return rate_limited_response(request, limit, "download")

    try:
        vip_user_id = None
        if is_vip and getattr(request, "user", None) is not None and request.user.is_authenticated:
            vip_user_id = request.user.pk

        session, token = create_generation_session(
            quality,
            site_url=config.wordpress_site_url,
            request=request,
            vip_user_id=vip_user_id,
        )
        handoff_url = build_handoff_url(config, token)

        config.last_handoff_at = timezone.now()
        config.last_handoff_error = ""
        config.save(update_fields=["last_handoff_at", "last_handoff_error", "updated_at"])

        log_event(
            "handoff_started",
            message=f"Visitor sent to the generator for quality #{quality.pk}.",
            session=session,
            api_key_prefix=config.api_key_prefix,
            ip_hash=session.created_ip_hash,
        )

        response = HttpResponseRedirect(handoff_url)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        # The token lives in the WordPress URL: never leak it via Referer.
        response["Referrer-Policy"] = "no-referrer"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response
    except ApiFailure as exc:
        logger.warning("External link handoff skipped: %s", exc.message)
        try:
            _note_handoff_error(get_config(), exc.message)
        except Exception:  # pragma: no cover
            logger.exception("Could not record handoff error")
        return None
    except Exception:  # pragma: no cover - a broken integration must not break downloads
        logger.exception("External link handoff failed; falling back to the direct download")
        try:
            _note_handoff_error(get_config(), "Internal error while creating the handoff.")
        except Exception:
            pass
        return None


def _note_handoff_error(config, message):
    message = (message or "")[:200]
    if config.last_handoff_error == message:
        return
    config.last_handoff_error = message
    config.save(update_fields=["last_handoff_error", "updated_at"])
    log_event(
        "handoff_skipped",
        level=ExternalLinkEvent.Level.WARNING,
        message=f"Handoff skipped, direct download still works: {message}",
        api_key_prefix=config.api_key_prefix,
    )


def _load_session(token):
    if not SESSION_TOKEN_RE.match(token or ""):
        return None
    return ExternalLinkSession.objects.filter(token_hash=hash_session_token(token)).first()


def validate_generation_session(config, *, token, site_url, countdown_seconds=0, ctx=None):
    """Validate a session for the plugin's entry route and stamp ``not_before``."""
    now = timezone.now()
    session = _load_session(token)
    if session is None:
        raise ApiFailure("invalid_token", "This download session is not recognised.", 404)
    if session.site_url and not hosts_match(session.site_url, site_url):
        raise ApiFailure("domain_mismatch", "This session belongs to another installation.", 403)
    if session.redeemed_at is not None:
        raise ApiFailure("session_redeemed", "This download session has already been used.", 409)
    if session.status == ExternalLinkSession.Status.REVOKED:
        raise ApiFailure("session_revoked", "This download session has been revoked.", 409)
    if session.expires_at <= now:
        ExternalLinkSession.objects.filter(pk=session.pk).update(
            status=ExternalLinkSession.Status.EXPIRED
        )
        log_event(
            "session_expired",
            level=ExternalLinkEvent.Level.WARNING,
            message=f"Session {session.token_prefix}… expired before validation.",
            session=session,
            api_key_prefix=config.api_key_prefix,
        )
        raise ApiFailure("session_expired", "This download session has expired.", 410)

    try:
        requested = int(countdown_seconds or 0)
    except (TypeError, ValueError):
        requested = 0
    minimum = max(1, int(config.min_countdown_seconds or 1))
    maximum = max(minimum, int(config.max_countdown_seconds or minimum))
    countdown = min(max(requested, minimum), maximum)

    if session.not_before is None:
        deadline = now + timedelta(seconds=countdown)
        if deadline >= session.expires_at - timedelta(seconds=5):
            raise ApiFailure(
                "countdown_too_long",
                "The configured countdown does not fit inside the session lifetime.",
                409,
                max_countdown_seconds=max(
                    1, int((session.expires_at - now).total_seconds()) - 10
                ),
            )
        session.not_before = deadline
        session.validated_at = now
        session.status = ExternalLinkSession.Status.VALIDATED
        session.save(update_fields=["not_before", "validated_at", "status"])
        log_event(
            "session_validated",
            message=f"Session {session.token_prefix}… validated; countdown {countdown}s.",
            session=session,
            api_key_prefix=config.api_key_prefix,
            ip_hash=ctx.ip_hash if ctx else "",
        )

    remaining = max(0, int(math.ceil((session.not_before - timezone.now()).total_seconds())))
    return {
        "session": {
            "token_prefix": session.token_prefix,
            "quality": session.quality.quality if session.quality_id else "",
            "quality_label": session.quality_label,
            "status": session.status,
            "server_time": iso(timezone.now()),
            "not_before": iso(session.not_before),
            "expires_at": iso(session.expires_at),
            "remaining_seconds": remaining,
            "countdown_seconds": countdown,
            "can_redeem": session.is_redeemable,
        }
    }

def redeem_generation_session(config, *, token, site_url, ctx=None):
    """
    Atomically redeem a session and return the real download destination.

    Concurrency safety: the row is locked with ``SELECT ... FOR UPDATE`` inside a
    transaction *and* the state change is a conditional ``UPDATE`` that only
    matches an unredeemed row, so two simultaneous Proceed clicks cannot both
    succeed (the loser sees ``session_redeemed``).
    """
    now = timezone.now()
    session = _load_session(token)
    if session is None:
        raise ApiFailure("invalid_token", "This download session is not recognised.", 404)
    if session.site_url and not hosts_match(session.site_url, site_url):
        raise ApiFailure("domain_mismatch", "This session belongs to another installation.", 403)

    with transaction.atomic():
        session = (
            ExternalLinkSession.objects.select_for_update()
            .select_related("quality", "quality__post")
            .filter(pk=session.pk)
            .first()
        )
        if session is None:  # deleted between the two queries
            raise ApiFailure("invalid_token", "This download session is not recognised.", 404)
        if session.redeemed_at is not None:
            raise ApiFailure("session_redeemed", "This download session has already been used.", 409)
        if session.expires_at <= now:
            raise ApiFailure("session_expired", "This download session has expired.", 410)
        if session.status == ExternalLinkSession.Status.REVOKED:
            raise ApiFailure("session_revoked", "This download session has been revoked.", 409)
        if session.not_before is None:
            raise ApiFailure(
                "session_not_validated",
                "This session was never validated by the WordPress plugin.",
                409,
            )
        if session.not_before > now:
            wait = max(1, int(math.ceil((session.not_before - now).total_seconds())))
            raise ApiFailure(
                "too_early",
                "The countdown has not finished yet.",
                409,
                retry_after=wait,
                remaining_seconds=wait,
            )

        quality = session.quality
        if quality is None:
            raise ApiFailure(
                "quality_unavailable",
                "This download is no longer available.",
                410,
            )
        post = quality.post
        if post is None:
            raise ApiFailure("quality_unavailable", "This download is no longer available.", 410)
        if not post.enable_downloads:
            raise ApiFailure(
                "download_disabled", "Downloads are disabled for this item.", 410
            )
        if quality.is_premium and not _vip_still_active(session.vip_user_id):
            raise ApiFailure(
                "vip_required",
                "A VIP subscription is required for this download.",
                403,
            )

        claimed = ExternalLinkSession.objects.filter(
            pk=session.pk, redeemed_at__isnull=True
        ).update(
            redeemed_at=now,
            status=ExternalLinkSession.Status.REDEEMED,
            redeemed_ip_hash=ctx.ip_hash if ctx else "",
        )
        if not claimed:
            raise ApiFailure("session_redeemed", "This download session has already been used.", 409)

        destination = build_destination_url(quality, session.vip_user_id)

        # Count the download at delivery time (the visitor really continues to
        # the file), not when the button was clicked.
        from .models import DownloadQuality

        DownloadQuality.objects.filter(pk=quality.pk).update(
            download_count=F("download_count") + 1
        )

    log_event(
        "session_redeemed",
        message=f"Session {session.token_prefix}… redeemed; download released.",
        session=session,
        api_key_prefix=config.api_key_prefix,
        ip_hash=ctx.ip_hash if ctx else "",
    )
    return {
        "destination_url": destination,
        "quality": quality.quality,
        "quality_label": session.quality_label,
        "redeemed_at": iso(now),
        "expires_at": iso(session.expires_at),
    }


def _vip_still_active(user_id):
    if not user_id:
        return False
    try:
        from django.contrib.auth import get_user_model

        from subscriptions.utils import user_has_active_subscription

        user = get_user_model().objects.filter(pk=user_id).first()
        return bool(user and user_has_active_subscription(user))
    except Exception:  # pragma: no cover - entitlement helper must not break redemption
        logger.exception("VIP entitlement check failed")
        return False


def build_destination_url(quality, vip_user_id):
    """
    Reuse the existing download + VIP/CDN token helpers.

    The URL is calculated only here, at redemption time, from the current
    database row — it is never stored on the session, mirrored in HTML or logged.
    """
    destination = quality.download_url
    if vip_user_id:
        try:
            from subscriptions.utils import (
                append_vip_token_to_url,
                generate_cdn_vip_token,
                user_has_active_subscription,
            )
            from django.contrib.auth import get_user_model

            user = get_user_model().objects.filter(pk=vip_user_id).first()
            if user and user_has_active_subscription(user):
                destination = append_vip_token_to_url(
                    destination, generate_cdn_vip_token(user.pk)
                )
        except Exception:  # pragma: no cover - non-VIP download still works
            logger.exception("Could not attach the VIP token to the destination")
    return destination


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def statistics(days=7):
    now = timezone.now()
    window = now - timedelta(days=days)
    totals = ExternalLinkSession.objects.aggregate(
        total=Count("id"),
        redeemed=Count("id", filter=Q(redeemed_at__isnull=False)),
        expired=Count("id", filter=Q(redeemed_at__isnull=True, expires_at__lte=now)),
        pending=Count("id", filter=Q(redeemed_at__isnull=True, expires_at__gt=now)),
        recent=Count("id", filter=Q(created_at__gte=window)),
        recent_redeemed=Count("id", filter=Q(redeemed_at__gte=window)),
    )
    events = (
        ExternalLinkEvent.objects.filter(created_at__gte=window)
        .values("event")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    return {
        "sessions_total": totals["total"] or 0,
        "sessions_redeemed": totals["redeemed"] or 0,
        "sessions_expired": totals["expired"] or 0,
        "sessions_pending": totals["pending"] or 0,
        "sessions_recent": totals["recent"] or 0,
        "sessions_recent_redeemed": totals["recent_redeemed"] or 0,
        "events": list(events),
        "window_days": days,
    }


def recent_sessions(limit=25):
    return (
        ExternalLinkSession.objects.select_related("quality")
        .order_by("-created_at")[:limit]
    )


def recent_events(limit=25):
    return ExternalLinkEvent.objects.order_by("-created_at")[:limit]
