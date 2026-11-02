"""
HTTP client for the storage/uploader service (blog -> my.nzdworld.com).

Design notes
------------
* **Signing happens here, never in the browser.** The shared secret lives in the
  blog's server-side settings and is only ever used to build request headers.
* The canonical string is byte-for-byte identical to the server's
  ``uploader.services.api_auth.build_signature``; a mismatch would fail every
  call, so the two must be changed together.
* No automatic retries: a retried ``create_import`` could create a second job.
  The caller decides, using the returned error code.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings


class UploaderError(Exception):
    """A call to the uploader failed. ``code`` is stable for callers to branch on."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: int | None = None,
        payload: dict | None = None,
    ):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status
        self.payload = payload or {}


@dataclass(frozen=True)
class UploaderConfig:
    base_url: str
    key_id: str
    secret: str
    timeout: float
    enabled: bool


def get_config() -> UploaderConfig:
    return UploaderConfig(
        base_url=str(getattr(settings, "UPLOADER_API_URL", "")).rstrip("/"),
        key_id=str(getattr(settings, "UPLOADER_API_KEY_ID", "")),
        secret=str(getattr(settings, "UPLOADER_API_SECRET", "")),
        timeout=float(getattr(settings, "UPLOADER_TIMEOUT_SECONDS", 15)),
        enabled=bool(getattr(settings, "UPLOADER_ENABLED", False)),
    )


def is_configured() -> bool:
    config = get_config()
    return bool(config.enabled and config.base_url and config.key_id and config.secret)


def signing_key(secret: str) -> str:
    """Both sides derive the HMAC key the same way; only this hash is stored."""
    return hashlib.sha256((secret or "").encode("utf-8")).hexdigest()


def build_signature(
    *, key: str, method: str, path: str, query: str, body: bytes, timestamp: str, nonce: str
) -> str:
    canonical = "\n".join(
        [
            method.upper(),
            path or "/",
            query or "",
            hashlib.sha256(body or b"").hexdigest(),
            str(timestamp),
            str(nonce),
        ]
    )
    return hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def signed_headers(method: str, path: str, body: bytes, *, query: str = "") -> dict[str, str]:
    """Build the four authentication headers for one request."""
    config = get_config()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    signature = build_signature(
        key=signing_key(config.secret),
        method=method,
        path=path,
        query=query,
        body=body,
        timestamp=timestamp,
        nonce=nonce,
    )
    return {
        "X-NZD-Key": config.key_id,
        "X-NZD-Timestamp": timestamp,
        "X-NZD-Nonce": nonce,
        "X-NZD-Signature": signature,
    }


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    """
    Perform a signed call and normalise every failure into :class:`UploaderError`.

    ``path`` must include the API prefix (``/api/v1/...``) because the signature
    covers the exact request path the server will see.
    """
    config = get_config()
    if not config.enabled:
        raise UploaderError("disabled", "The uploader integration is disabled.")
    if not (config.base_url and config.key_id and config.secret):
        raise UploaderError("not_configured", "Uploader API credentials are missing.")

    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    headers = signed_headers(method, path, body)
    headers["Accept"] = "application/json"
    if body:
        headers["Content-Type"] = "application/json"

    url = f"{config.base_url}{path}"
    try:
        response = requests.request(
            method, url, data=body or None, headers=headers, timeout=config.timeout
        )
    except requests.Timeout as exc:
        raise UploaderError("timeout", f"The uploader did not answer within {config.timeout}s.") from exc
    except requests.RequestException as exc:
        raise UploaderError("connection_failed", f"Could not reach the uploader: {exc}") from exc

    try:
        data: Any = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        code = "http_error"
        message = f"The uploader returned HTTP {response.status_code}."
        if isinstance(data, dict):
            code = str(data.get("error") or code)
            message = str(data.get("message") or message)
        raise UploaderError(code, message, status=response.status_code, payload=data if isinstance(data, dict) else {})

    if not isinstance(data, dict):
        raise UploaderError("bad_response", "The uploader returned an unexpected payload.")
    return data


# ---------------------------------------------------------------------------
# Endpoint helpers
# ---------------------------------------------------------------------------
def create_import(
    *,
    source_url: str,
    title: str,
    filename_mode: str = "auto",
    requested_filename: str = "",
    created_by: str = "",
    url_type: str = "direct",
    provider: str = "",
) -> dict:
    return _call(
        "POST",
        "/api/v1/imports/",
        {
            "source_url": source_url,
            "title": title,
            "filename_mode": filename_mode,
            "requested_filename": requested_filename,
            "created_by": created_by,
            "url_type": url_type,
            "provider": provider,
        },
    )


def import_status(public_id: str) -> dict:
    return _call("GET", f"/api/v1/imports/{public_id}/")


def cancel_import(public_id: str) -> dict:
    return _call("POST", f"/api/v1/imports/{public_id}/cancel/", {})


def retry_import(public_id: str) -> dict:
    return _call("POST", f"/api/v1/imports/{public_id}/retry/", {})


def preview_filename(
    *, original_filename: str, title: str, manual_name: str = ""
) -> dict:
    return _call(
        "POST",
        "/api/v1/filenames/preview/",
        {
            "original_filename": original_filename,
            "title": title,
            "manual_name": manual_name,
        },
    )


def health() -> dict:
    config = get_config()
    try:
        response = requests.get(f"{config.base_url}/healthz", timeout=config.timeout)
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise UploaderError("connection_failed", f"Health check failed: {exc}") from exc

def verify_url(url: str) -> dict:
    """Lightweight HEAD/ranged-GET check performed by the storage service."""
    return _call("POST", "/api/v1/urls/verify/", {"url": url})