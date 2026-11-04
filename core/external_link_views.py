"""
HTTP endpoints for the External Link Generator integration.

All endpoints are machine-to-machine calls from the WordPress plugin. They are
authenticated with a Bearer API key plus an HMAC signature over the timestamp,
a single-use nonce and the exact request body — never with cookies — so they are
CSRF-exempt by design and never depend on (or leak) a logged-in browser session.

Public (unauthenticated) surface: ``GET /api/external-link/`` only, which returns
static documentation metadata and no configuration or destination data.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import external_link_service as service
from .external_link_models import ExternalLinkEvent
from .models import DownloadQuality


def _json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def _fail(failure, *, event, ctx=None, request=None):
    """Log a controlled failure (code + short message, never the token) and reply."""
    level = (
        ExternalLinkEvent.Level.WARNING
        if failure.status < 500
        else ExternalLinkEvent.Level.ERROR
    )
    service.log_event(
        event,
        level=level,
        message=f"{failure.code}: {failure.message}",
        api_key_prefix=(ctx.config.api_key_prefix if ctx else ""),
        ip_hash=(ctx.ip_hash if ctx else service.client_ip_hash(request) if request else ""),
    )
    return service.error_response(failure, retry_after=failure.extra.get("retry_after"))


@require_GET
def api_index(request):
    """Static API description (safe to expose; contains no configuration)."""
    return _json(
        {
            "name": "NZDWorld External Link Generator API",
            "api_version": "1.0",
            "authentication": {
                "scheme": "Bearer API key + HMAC-SHA256 request signature",
                "headers": [
                    "Authorization: Bearer <api key>",
                    "X-NZD-Timestamp: <unix seconds>",
                    "X-NZD-Nonce: <unique random string>",
                    "X-NZD-Signature: hex(hmac_sha256(key, timestamp + '.' + nonce + '.' + sha256(body)))",
                ],
                "transport": "HTTPS only",
            },
            "endpoints": [
                {"method": "POST", "path": "/api/external-link/connection-test/"},
                {"method": "POST", "path": "/api/external-link/sessions/create/"},
                {"method": "POST", "path": "/api/external-link/sessions/validate/"},
                {"method": "POST", "path": "/api/external-link/sessions/redeem/"},
            ],
            "notes": (
                "Session creation also happens automatically when a visitor opens "
                "/download/quality/<id>/ while the integration is enabled. Download "
                "destinations are only returned by the redeem endpoint, after the "
                "session is validated, the server-side countdown has elapsed and the "
                "session is atomically marked as redeemed."
            ),
        }
    )


@csrf_exempt
@require_POST
def connection_test(request):
    """
    Verify a WordPress installation and register its site URL + entry template.

    Allowed while the generator is disabled so the plugin can report a precise
    status ("connected, generator disabled") instead of an opaque auth error.
    """
    ctx, error = service.authenticate_api_request(request, allow_when_disabled=True)
    if error is not None:
        return error
    try:
        payload = service.register_connection(ctx)
    except service.ApiFailure as failure:
        ctx.config.last_connection_ok = False
        ctx.config.last_connection_error = failure.message[:200]
        ctx.config.save(update_fields=["last_connection_ok", "last_connection_error", "updated_at"])
        return _fail(failure, event="connection_failed", ctx=ctx)
    return _json({"ok": True, **payload})


@csrf_exempt
@require_POST
def session_create(request):
    """
    Create a generation session for a specific download-quality row.

    The normal visitor flow creates its session from
    ``/download/quality/<id>/`` (where the visitor's own subscription state can
    be checked). This endpoint exists for server-initiated integrations; it
    refuses premium rows because there is no visitor identity to authorise.
    """
    ctx, error = service.authenticate_api_request(request)
    if error is not None:
        return error
    try:
        raw_quality_id = ctx.data.get("quality_id")
        if isinstance(raw_quality_id, bool):
            raise service.ApiFailure("invalid_quality", "quality_id must be a positive integer.", 400)
        try:
            quality_id = int(str(raw_quality_id).strip())
        except (TypeError, ValueError):
            raise service.ApiFailure("invalid_quality", "quality_id must be a positive integer.", 400)
        if quality_id <= 0:
            raise service.ApiFailure("invalid_quality", "quality_id must be a positive integer.", 400)

        quality = (
            DownloadQuality.objects.select_related("post").filter(pk=quality_id).first()
        )
        if quality is None:
            raise service.ApiFailure("quality_unavailable", "This download is no longer available.", 404)
        if quality.post is None or not quality.post.enable_downloads:
            raise service.ApiFailure("download_disabled", "Downloads are disabled for this item.", 410)
        if quality.is_premium:
            raise service.ApiFailure(
                "premium_not_allowed",
                "Premium downloads can only be started by a signed-in visitor on NZDWorld.",
                403,
            )

        session, token = service.create_generation_session(
            quality, site_url=ctx.site_url, request=request
        )
    except service.ApiFailure as failure:
        return _fail(failure, event="session_create_failed", ctx=ctx)

    return _json(
        {
            "ok": True,
            "token": token,
            "session": {
                "token_prefix": session.token_prefix,
                "status": session.status,
                "quality": quality.quality,
                "quality_label": session.quality_label,
                "expires_at": service.iso(session.expires_at),
                "not_before": None,
            },
        },
        status=201,
    )


@csrf_exempt
@require_POST
def session_validate(request):
    """
    Validate an incoming token and stamp the server-side countdown deadline.

    The plugin calls this from its entry route, after the visitor was redirected
    from NZDWorld. Repeating the call (e.g. on refresh) returns the same
    ``not_before`` instead of restarting the countdown.
    """
    ctx, error = service.authenticate_api_request(request)
    if error is not None:
        return error
    try:
        token = str(ctx.data.get("token") or "").strip()
        if not service.SESSION_TOKEN_RE.match(token):
            raise service.ApiFailure("invalid_token", "This download session is not recognised.", 404)
        payload = service.validate_generation_session(
            ctx.config,
            token=token,
            site_url=ctx.site_url,
            countdown_seconds=ctx.data.get("countdown_seconds") or 0,
            ctx=ctx,
        )
    except service.ApiFailure as failure:
        return _fail(failure, event="session_validate_failed", ctx=ctx)
    return _json({"ok": True, **payload})


@csrf_exempt
@require_POST
def session_redeem(request):
    """
    Atomically redeem a session and release the (previously hidden) destination.

    Refused before the server-side ``not_before`` time, after expiry, on a second
    use, for a deleted/disabled quality row and for a session belonging to a
    different installation.
    """
    ctx, error = service.authenticate_api_request(request)
    if error is not None:
        return error
    try:
        token = str(ctx.data.get("token") or "").strip()
        if not service.SESSION_TOKEN_RE.match(token):
            raise service.ApiFailure("invalid_token", "This download session is not recognised.", 404)
        payload = service.redeem_generation_session(
            ctx.config, token=token, site_url=ctx.site_url, ctx=ctx
        )
    except service.ApiFailure as failure:
        return _fail(failure, event="session_redeem_failed", ctx=ctx)
    return _json({"ok": True, **payload})

