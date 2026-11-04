# NZDWorld External Link Generator — API Documentation

Version **1.0** · Base URL `https://nzdworld.com/api/external-link/`

This API is used **only** by the `nzdworld-link-generator` WordPress plugin. It is
server-to-server, HTTPS-only, and authenticated with an API key plus an HMAC
request signature. It is never called from a browser and never uses cookies, so
it is CSRF-exempt by design.

The browser handoff itself is not part of this API (see
[Browser handoff](#7-browser-handoff-not-an-api-call)): a visitor click creates a
session on NZDWorld, and only the plugin's server can redeem it.

---

## 1. Overview

```
Visitor clicks /download/quality/16874/
        │
        ▼
NZDWorld creates a session (opaque token) ──302──▶ WordPress entry URL
        │                                             │
        │                              POST sessions/validate/  (signed)
        │                                             │  ◀── not_before, expires_at
        │                                             ▼
        │                        random published article + ?nzd_gen=<opaque-id>
        │                                             │
        │                                    countdown (server-enforced)
        │                                             │
        │                              POST sessions/redeem/  (signed)
        ▼                                             │
 atomic UPDATE ... WHERE redeemed_at IS NULL ─────────┘
        │
        └──▶ {"destination_url": "https://..."} ──▶ browser redirect
```

Key properties:

* The destination URL is returned **only** by `sessions/redeem/`, only after the
  server-side `not_before` time and only once.
* Tokens are opaque, short-lived and stored **hashed** on NZDWorld.
* API keys are stored as a SHA-256 digest; the plaintext is shown once in the
  admin screen.
* Every request must be HTTPS; plain HTTP is rejected before authentication.

---

## 2. Authentication

### 2.1 Headers

| Header | Required | Value |
| --- | --- | --- |
| `Content-Type` | yes | `application/json` |
| `Authorization` | yes | `Bearer nzd_live_…` |
| `X-NZD-Timestamp` | yes | Unix time in **seconds**, as a string |
| `X-NZD-Nonce` | yes | Random string, 8–64 chars `[A-Za-z0-9_-]`, unique per request |
| `X-NZD-Signature` | yes | Lowercase hex HMAC-SHA256 (see below) |

### 2.2 Signature

```
body_sha256 = hex( sha256( raw_request_body ) )
payload     = timestamp + "." + nonce + "." + body_sha256
signature   = hex( hmac_sha256( key = API_KEY, message = payload ) )
```

The raw body must be signed **exactly** as sent (byte for byte). A signed request
is accepted for `api_clock_skew_seconds` (default **300 s**, admin-configurable)
and each nonce is stored: a second request with the same nonce is rejected with
`replayed_request`.

Python:

```python
import hashlib, hmac
body_hash = hashlib.sha256(raw_body).hexdigest()
signature = hmac.new(key.encode(), f"{ts}.{nonce}.{body_hash}".encode(), hashlib.sha256).hexdigest()
```

PHP (this is what the plugin does):

```php
$body_hash = hash('sha256', $body);
$signature = hash_hmac('sha256', $ts . '.' . $nonce . '.' . $body_hash, $api_key);
```

### 2.3 Installation binding

Every body must include `site_url`, an absolute HTTPS URL of the WordPress
installation. When **Domain lock** is on (default) and a site is registered, the
host must match the registered host, otherwise the call fails with
`domain_mismatch` (HTTP 403). The first successful `connection-test/` registers
the site and the `entry_url_template` it reports (trust on first use).

### 2.4 Rate limits

| Scope | Limit | Window | Response |
| --- | --- | --- | --- |
| API calls (per key prefix + IP) | 240 | 300 s | `429 rate_limited` + `Retry-After` |
| Browser handoff (per visitor IP) | 30 | 300 s | 429 page (normal download not attempted) |

Both limits are configurable in `settings.RATE_LIMITS`
(`external_api`, `external_handoff`).

---

## 3. Responses

### 3.1 Success envelope

```json
{ "ok": true, ... }
```

`Cache-Control: no-store, no-cache, must-revalidate, private` is always sent.

### 3.2 Error envelope

```json
{
  "ok": false,
  "error": {
    "code": "session_expired",
    "message": "This download session has expired."
  }
}
```

Some errors add fields: `retry_after` and `remaining_seconds` (seconds until
redemption is allowed) for `too_early`; `max_countdown_seconds` for
`countdown_too_long`.

| Code | HTTP | Meaning |
| --- | --- | --- |
| `insecure_transport` | 400 | Request was not HTTPS |
| `invalid_json` | 400 | Body is not a JSON object |
| `invalid_site` | 400 | `site_url` missing or not absolute HTTPS |
| `invalid_entry_url` | 400 | `entry_url_template` invalid (not HTTPS / no `{token}` / wrong host) |
| `invalid_quality` | 400 | `quality_id` missing or not a positive integer |
| `unauthorized` | 401 | Missing, invalid, revoked or expired API key |
| `invalid_signature` | 401 | Signature headers missing or the HMAC does not match |
| `stale_request` | 401 | Timestamp outside the allowed clock window |
| `replayed_request` | 401 | Nonce already used |
| `integration_disabled` | 403 | Feature switched off on NZDWorld |
| `domain_mismatch` | 403 | Caller host is not the registered installation |
| `premium_not_allowed` | 403 | `sessions/create/` cannot start premium downloads |
| `vip_required` | 403 | Premium download without an active VIP subscription |
| `invalid_token` | 404 | Token is malformed or unknown |
| `quality_unavailable` | 404/410 | Download-quality row missing/deleted |
| `session_redeemed` | 409 | Session already used |
| `session_revoked` | 409 | Session revoked by an administrator |
| `session_not_validated` | 409 | `sessions/validate/` was never called |
| `too_early` | 409 | Countdown not finished yet (`retry_after` included) |
| `countdown_too_long` | 409 | Countdown does not fit inside the session lifetime |
| `not_configured` | 409 | WordPress entry URL missing or invalid |
| `session_expired` | 410 | Session lifetime over |
| `download_disabled` | 410 | Downloads disabled for that post |
| `rate_limited` | 429 | Too many requests (`Retry-After` included) |
| `server_error` | 500/502 | Unexpected server failure (not JSON when Django returns a 500 page) |

---

## 4. Endpoints

### 4.1 `GET /api/external-link/`

Public metadata (no authentication, no configuration values).

Response `200`:

```json
{
  "name": "NZDWorld External Link Generator API",
  "api_version": "1.0",
  "authentication": { "...": "..." },
  "endpoints": [
    {"method": "POST", "path": "/api/external-link/connection-test/"},
    {"method": "POST", "path": "/api/external-link/sessions/create/"},
    {"method": "POST", "path": "/api/external-link/sessions/validate/"},
    {"method": "POST", "path": "/api/external-link/sessions/redeem/"}
  ],
  "notes": "..."
}
```

### 4.2 `POST /api/external-link/connection-test/`

Verifies the credentials, registers/validates the installation and reports the
limits the plugin must respect. Allowed while the generator is disabled, so the
plugin can display an accurate status.

Request:

```json
{
  "site_url": "https://blog.example.com",
  "entry_url_template": "https://blog.example.com/nzdworld-link/{token}/",
  "plugin_version": "1.0.0",
  "site_name": "NZDWorld Jobs",
  "countdown_seconds": 20
}
```

Response `200`:

```json
{
  "ok": true,
  "api_version": "1.0",
  "server_time": "2026-01-01T12:00:00Z",
  "integration_enabled": true,
  "registered_site": "https://blog.example.com",
  "entry_template_ok": true,
  "session_ttl_seconds": 900,
  "min_countdown_seconds": 5,
  "max_countdown_seconds": 120,
  "message": "Connection verified."
}
```

When `integration_enabled` is `false` the call still succeeds; the plugin shows
"connected, generator disabled on NZDWorld".

### 4.3 `POST /api/external-link/sessions/create/`

Creates a session server-side for a specific download-quality row. The normal
visitor flow creates its session from `/download/quality/<id>/` (where the
visitor's own subscription state can be checked); this endpoint exists for
server-initiated integrations. Premium rows are refused because there is no
visitor identity to authorise.

Request:

```json
{ "site_url": "https://blog.example.com", "quality_id": 16874 }
```

Response `201`:

```json
{
  "ok": true,
  "token": "kK3…opaque…9Q",
  "session": {
    "token_prefix": "kK3eQ1Zr9A",
    "status": "created",
    "quality": "720p",
    "quality_label": "DOWNLOAD MOVIE [720p (HD)] — Some Movie",
    "expires_at": "2026-01-01T12:15:00Z",
    "not_before": null
  }
}
```

The token is returned **only** to the authenticated caller. The plugin stores it
encrypted; NZDWorld stores only its SHA-256 digest.

### 4.4 `POST /api/external-link/sessions/validate/`

Validates an incoming entry token and stamps the server-side countdown deadline.
Returns the same `not_before` on every repeat call (page refresh), so the
countdown never restarts.

Request:

```json
{
  "site_url": "https://blog.example.com",
  "token": "kK3…opaque…9Q",
  "countdown_seconds": 30
}
```

Response `200`:

```json
{
  "ok": true,
  "session": {
    "token_prefix": "kK3eQ1Zr9A",
    "quality": "720p",
    "quality_label": "DOWNLOAD MOVIE [720p (HD)] — Some Movie",
    "status": "validated",
    "server_time": "2026-01-01T12:00:02Z",
    "not_before": "2026-01-01T12:00:32Z",
    "expires_at": "2026-01-01T12:15:00Z",
    "remaining_seconds": 30,
    "countdown_seconds": 30,
    "can_redeem": false
  }
}
```

Rules:

* `countdown_seconds` is clamped into `[min_countdown_seconds, max_countdown_seconds]`.
* The first successful call sets `not_before = now + countdown`; later calls never
  move it.
* If `not_before + 5 s` would exceed `expires_at`, the call fails with
  `countdown_too_long` (HTTP 409).
* Expired sessions answer `410 session_expired`; redeemed sessions answer
  `409 session_redeemed`; sessions from another installation answer
  `403 domain_mismatch`.

### 4.5 `POST /api/external-link/sessions/redeem/`

Atomically claims the session and returns the real download destination. This is
the **only** endpoint that ever returns the destination.

Request:

```json
{ "site_url": "https://blog.example.com", "token": "kK3…opaque…9Q" }
```

Response `200`:

```json
{
  "ok": true,
  "destination_url": "https://cdn.example.com/files/movie-720p.mp4?token=…",
  "quality": "720p",
  "quality_label": "DOWNLOAD MOVIE [720p (HD)] — Some Movie",
  "redeemed_at": "2026-01-01T12:00:35Z",
  "expires_at": "2026-01-01T12:15:00Z"
}
```

Refusals:

| Situation | Response |
| --- | --- |
| Before `not_before` | `409 too_early`, `retry_after` / `remaining_seconds` |
| Never validated | `409 session_not_validated` |
| Already redeemed (including concurrent double click) | `409 session_redeemed` |
| Expired | `410 session_expired` |
| Quality row deleted | `410 quality_unavailable` |
| Post downloads disabled | `410 download_disabled` |
| Premium without active VIP | `403 vip_required` |

Concurrency: the row is locked with `SELECT … FOR UPDATE` inside a transaction
**and** claimed with `UPDATE … WHERE redeemed_at IS NULL`; only one caller can
ever receive the URL. A concurrent loser gets `session_redeemed`.

VIP/CDN tokens are generated at redemption from the visitor's user id captured
during the handoff, using the existing `subscriptions.utils` helpers.

---

## 5. Session lifecycle

```
created ──validate──▶ validated ──redeem──▶ redeemed
   │                      │                    (terminal)
   │                      └──expires──▶ expired (terminal)
   └──admin revoke───────────────▶ revoked (terminal)
```

| Field | Set at | Meaning |
| --- | --- | --- |
| `expires_at` | creation | Hard lifetime (default 900 s, max 3600 s) |
| `not_before` | first validation | Earliest redemption time (server-side countdown) |
| `validated_at` | first validation | When the plugin picked up the session |
| `redeemed_at` | redemption | Set exactly once, atomically |
| `status` | all steps | `created`, `validated`, `redeemed`, `expired`, `revoked` |

The plugin also keeps its own session row, bound to the selected article, the
visitor's browser (hashed cookie, or IP within 120 s) and the encrypted token.
That row is what makes the popup appear — and only on the session's own page.

---

## 6. Timestamps and clocks

* All API timestamps are ISO-8601 UTC with a `Z` suffix.
* `X-NZD-Timestamp` is Unix time in **seconds**; the accepted skew defaults to
  300 s and is configurable in the admin (`api_clock_skew_seconds`).
* If either server's clock drifts, calls fail with `stale_request`. Fix NTP.

---

## 7. Browser handoff (not an API call)

`GET /download/quality/<id>/` (existing route) keeps its behaviour, with one
optional step in the middle:

1. Resolve `<id>` exactly as before (`DownloadQuality` row) and apply the
   existing premium/VIP gate and rate limit.
2. If the integration is enabled and fully configured, create a session for that
   exact quality row and answer `302` to the WordPress entry URL:
   `https://blog.example.com/nzdworld-link/<token>/`
   with `Cache-Control: no-store`, `Referrer-Policy: no-referrer` and
   `X-Robots-Tag: noindex`.
3. Otherwise (disabled, unconfigured, error, premium without VIP) continue with
   the original `302` to the download URL — unchanged behaviour.

The WordPress entry URL template is reported by the plugin and stored by NZDWorld
(admin-overridable). It must be HTTPS, on the registered host, and contain exactly
one `{token}` placeholder.

---

## 8. Compatibility and versioning

* `api_version` is `"1.0"`. Additive fields may appear in responses; clients must
  ignore unknown keys.
* Breaking changes would be published under a new version and a new path.
* The plugin and NZDWorld agree on the signature format; the same fixed test
  vector is asserted in both test suites (Django `CrossImplementationSignatureTests`,
  plugin `tests/run-tests.php`).
