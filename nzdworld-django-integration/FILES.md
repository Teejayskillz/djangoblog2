# Changed Django files

All paths are relative to the project root (`djangoblog2/`).

## Added

| File | Lines | Purpose |
| --- | --- | --- |
| `core/external_link_models.py` | ~346 | `ExternalLinkConfig` (singleton: enabled flag, hashed API key + prefix, registered site + entry template, TTL/countdown policy, connection state, counters), `ExternalLinkSession` (token digest + prefix, quality FK with `SET_NULL`, quality/post snapshots, visitor VIP user, status, created/expires/validated/not_before/redeemed timestamps, IP hashes), `ExternalLinkEvent` (audit trail without secrets), `ExternalLinkNonce` (replay protection). |
| `core/external_link_service.py` | ~1 060 | Key issue/rotate/revoke, key hashing and constant-time verification, site/entry-URL normalisation, HTTPS check, signed-request authentication (Bearer + HMAC + timestamp + nonce + domain binding + rate limit), connection registration, session creation/validation/redemption (atomic single use), destination construction reusing `subscriptions.utils` VIP helpers, event logging, statistics. |
| `core/external_link_views.py` | ~216 | `api_index`, `connection_test`, `session_create`, `session_validate`, `session_redeem`. CSRF-exempt, POST-only, JSON envelopes, no secrets in responses. |
| `core/external_link_urls.py` | ~15 | Mounts the four endpoints plus the read-only index. |
| `core/external_link_forms.py` | ~113 | Shared `ExternalLinkConfigForm`: HTTPS site URL, entry-template validation, TTL 120–3600, countdown limits, TTL must exceed max countdown by ≥ 30 s, clock-skew bounds. |
| `core/external_link_admin.py` | ~380 | Django-admin registration: config with read-only key/connection/statistics panels and actions (generate/rotate key, revoke, enable, disable, forget site), read-only session list (revoke/expire actions), event log, nonce log. |
| `core/migrations/0017_external_link_generator.py` | ~117 | Creates the four tables/indexes. Depends on `core.0016`. |
| `core/test_external_links.py` | ~781 | 53 automated tests, including 6 CSRF-compatibility tests (see `TEST-REPORT.md`). |
| `controlcenter/external_link_views.py` | ~164 | Operator screen view (`StaffRequiredMixin` + `core.change_externallinkconfig`): save settings, generate/rotate key (plaintext shown once), revoke, forget site, enable/disable, prune abandoned sessions, statistics, recent sessions/events. |
| `controlcenter/templates/controlcenter/external_link_generator.html` | ~302 | The operator screen itself (status badges, key-once panel, settings form, connection info, statistics, sessions/events tables, copy-to-clipboard). |

## Modified

| File | Change | Risk |
| --- | --- | --- |
| `blog_project/settings.py` | `EXTERNAL_LINK_REQUIRE_HTTPS`, `EXTERNAL_LINK_API_BASE`, two `RATE_LIMITS` scopes (`external_handoff`, `external_api`), and `OpaqueOriginCsrfCompatMiddleware` inserted **above** `CsrfViewMiddleware`. | Additive; middleware only rewrites an explicit `Origin: null` on admin POSTs, and the CSRF token is still required. |
| `blog_project/urls.py` | `path('api/external-link/', include('core.external_link_urls'))` placed **before** `core.urls` (whose generic slug patterns would otherwise swallow the API paths). | Additive, ordered correctly. |
| `core/models.py` | Imports the new models at the end of the file so they belong to the existing `core` app (no new app, no `INSTALLED_APPS` change, migration graph unchanged apart from `0017`). | Import-only. |
| `core/admin.py` | Imports `core.external_link_admin` at the end (registers the new ModelAdmins). | Import-only. |
| `core/views.py` | In `download_quality()`: after the existing premium/VIP gate and before the counter/redirect, calls `start_handoff(request, quality, is_vip=is_vip)` and returns its response when it is not `None`. ~10 lines. | When the feature is disabled/unconfigured it returns `None`, so the original lines execute unchanged. |
| `controlcenter/urls.py` | Adds `external-link-generator/` **before** the generic `<app_label>/<model_name>/` routes. | Additive, ordered correctly. |
| `controlcenter/templates/controlcenter/base.html` | Adds one sidebar item ("Link Generator"). | Markup-only. |
| `controlcenter/middleware.py` | New `OpaqueOriginCsrfCompatMiddleware`: when a browser reports `Origin: null` (sandboxed preview iframe / in-app browser), the value is normalised to the site's own origin for Control Center and private-admin POSTs only, so Django's CSRF token check can run instead of failing on the origin. Public views, the API and the rest of the site are untouched. | Narrowly scoped; a foreign origin is still rejected, and a missing/invalid CSRF token still returns 403. |

## Not changed

* No existing model, route, template (other than the sidebar), download button,
  file-upload system, quality record, subtitle flow, premium/VIP rule, analytics
  or admin screen was replaced or removed.
* The private Django admin remains mounted at `/django-admin-backend-internal/`;
  the new screens are registered there *and* in the Control Center.
* `core/urls.py` and `download_quality`'s URL remain exactly as before.
