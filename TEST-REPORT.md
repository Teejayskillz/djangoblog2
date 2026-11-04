# Test Report — NZDWorld External Link Generator

Date: 2026-11-04
Branch: `feature/external-link-generator` (baseline preserved on `backup/pre-external-link-generator`)

This report lists **only what was actually executed** in this environment, plus a
manual checklist for the parts that need a real PHP/WordPress runtime.

---

## 1. Environment

| Item | Value |
| --- | --- |
| OS | Windows 10/11 (PowerShell 5.1) |
| Python | 3.14.7 |
| Django | 5.0.6 (project requirements pin 4.2.13; both support the code used) |
| Database (tests) | SQLite in-memory test database (`file:memorydb_default?mode=memory&cache=shared`) |
| Database (dev) | `db.sqlite3` — **not migrated** during testing (only `--plan`/`sqlmigrate` were used) |
| PHP | **not installed** on this machine |
| Node.js / npm | **not installed** |
| Network from shell | **unavailable** (cannot download PHP or reach a live WordPress) |
| WordPress / WP-CLI | not available |

Because there is no PHP runtime, no `php -l`, no WordPress test suite and no
browser rendering could be executed here. Everything that *can* be verified
offline was verified, and the gaps are listed explicitly in §7–§9.

---

## 2. Commands executed and results

| # | Command | Result |
| --- | --- | --- |
| 1 | `python manage.py check` | `System check identified no issues (0 silenced).` |
| 2 | `python manage.py makemigrations --check --dry-run` | `No changes detected` (migration matches models) |
| 3 | `python manage.py test core.test_external_links -v 1` | **53 tests, OK** (43.9 s) |
| 4 | `python manage.py test` (whole project) | **329 tests, OK**, 468.066 s, process exit code **0** |
| 5 | `python manage.py migrate --plan` | Lists pending migrations including `core.0017_external_link_generator` (read-only; dev DB untouched) |
| 6 | `python manage.py sqlmigrate core 0017` | Renders valid SQLite DDL for the four tables |
| 7 | `python wordpress-plugin/package-plugin.py` | Built + verified `nzdworld-link-generator.zip` |
| 8 | `python wordpress-plugin/check-php-structure.py` | 16 PHP/JS/CSS files checked, no structural problems |
| 9 | `git diff --stat` for the Django patch | 18 files, +3 638 / −1 lines |

Earlier runs also finished with **262 tests, OK** (`core controlcenter subscriptions
sports ads`) and **323 tests, OK** (whole project) before the CSRF compatibility
fix was added; the final whole-project run (#4) is the authoritative one.

### Evidence for #3 / #4 (tail of output)

```
Ran 53 tests in 43.922s
OK
```

```
Ran 329 tests in 468.066s
OK
Destroying test database for alias 'default'...
EXITCODE=0
```

---

## 3. Django feature tests (53)

File: `core/test_external_links.py`. Each test uses a real `DownloadQuality` row
attached to a real `Post` (with a category), a real config singleton and a real
generated API key; HTTP calls go through Django's test client with signed
headers.

| Class | Tests | What it proves |
| --- | --- | --- |
| `DisabledIntegrationTests` | 4 | Disabled feature keeps the original 302 to the download URL and still increments `download_count`; revoked key → original behaviour; enabled-but-unconfigured falls back and records the reason; disabled API rejects sessions but still allows a connection test. |
| `HandoffTests` | 7 | Enabled handoff 302s to the WordPress entry URL with an opaque token; token is not the quality id, is not stored in plaintext; response has `no-store` + `Referrer-Policy: no-referrer` and an empty body; two clicks create two independent single-use sessions; premium without VIP keeps the existing plans-page redirect; VIP handoff stores the user id and redemption reuses `append_vip_token_to_url`; handoff rate limit returns 429; no token/destination/API key ever appears in the audit events. |
| `ApiAuthenticationTests` | 9 | Public index leaks no secrets; HTTPS enforced; wrong key rejected + audited without logging the key; revoked key rejected; missing signature headers rejected; tampered body rejected; stale timestamp rejected; replayed nonce rejected; foreign domain rejected without changing the registration. |
| `CrossImplementationSignatureTests` | 1 | Fixed HMAC vector `a66ff43b…6775` matches the PHP implementation (same vector in `tests/run-tests.php`). |
| `ConnectionTests` | 4 | First connection registers site + entry template + plugin version; entry template on another host refused; template without `{token}` refused; `http://` site URL refused. |
| `SessionLifecycleTests` | 14 | Validate stamps `not_before` (≈ requested countdown), repeat validate does not move it, countdown clamped to the configured maximum, countdown that cannot fit the lifetime rejected, unknown token 404, expired session 410 on validate and redeem, early redeem 409 `too_early` with `retry_after` and no destination, redeem without validation 409, redeem after countdown returns the destination exactly once and increments the counter once, replay 409, re-entering the entry token after redemption 409, deleted quality 410, `enable_downloads=False` 410, session from another installation 403, admin-revoked session 409. |
| `SessionCreateEndpointTests` | 3 | `sessions/create/` returns a token; refuses premium rows; validates `quality_id`. |
| `OpaqueOriginCsrfTests` | 6 | Browsers that report an opaque origin (`Origin: null` — sandboxed preview iframes, in-app browsers) can still use the admin: the action succeeds with a valid CSRF token, a missing token is still 403, a foreign origin is still 403, other Control Center screens are covered, a public view (login) is **not** relaxed, and the private Django-admin action works the same way. |
| `AdminSurfaceTests` | 5 | Control Center screen: anonymous → login redirect, staff without permission → 403, superuser → 200; key generation shows the plaintext key exactly once (second GET does not contain it) and its digest matches the stored hash; enable/disable actions persist; form rejects `http://` site URL and too-short TTL; Django-admin registration exposes the five actions and the custom read-only columns; `has_add_permission` is False for the singleton. |

Notable behaviour verified in passing: the event log table never contains the
token, the destination host, or the API key.

---

## 4. WordPress plugin artefacts verified offline

| Check | Method | Result |
| --- | --- | --- |
| Plugin PHP/JS/CSS delimiter and structure | `wordpress-plugin/check-php-structure.py` (comment/string/regex-aware tokeniser, nesting order + line numbers) | 16 files, **no problems** |
| Plugin header | Script checks `Plugin Name:`, `Version:`, `License:`, `Text Domain:` in the first bytes | present |
| `require_once` targets | Script resolves every include in the main plugin file | all exist |
| Expected file list | Script compares against the 17 required files | complete |
| ZIP path separators | Packaging script rejects any `\` in entry names | forward slashes only |
| ZIP plugin root | Packaging script checks every entry starts with `nzdworld-link-generator/` | correct |
| ZIP contents vs source | Byte-for-byte comparison of every entry with the source file | identical |
| ZIP integrity | `zipfile.testzip()` | no corrupt entries |
| ZIP extraction | Extracted to a temp folder with Python `zipfile` | `nzdworld-link-generator/` with `includes/`, `templates/`, `assets/`, `tests/`, main file, readme, uninstall |

Final archive:

```
nzdworld-link-generator.zip — 44 267 bytes, 17 files (+5 directory entries)
sha256 bb7a25baded95896db93fd2aa4cc270b7597b637b82b1ede17cd557c9d542f85
```

> Note: the first packaging attempt used Windows `Compress-Archive`, which wrote
> `nzdworld-link-generator\includes\...` (backslashes). PHP's `ZipArchive` treats
> those as literal file names, so the plugin would have extracted broken. The
> archive was rebuilt with the Python packaging script and this regression is now
> checked automatically.

### PHP logic test harness (provided, **not executed here**)

`wordpress-plugin/nzdworld-link-generator/tests/run-tests.php` contains 38
standalone checks (34 when OpenSSL is unavailable) (signature vector, error mapping, destination URL validation,
entry template, token patterns, timestamp parsing, status mapping, integer
clamping, URL sanitising, AES-GCM round-trip and tamper detection, hashing,
public-id format, same-site checks, visitor messages). Run it on any host with
PHP:

```bash
cd wp-content/plugins/nzdworld-link-generator
php tests/run-tests.php
```

It is **not** a substitute for `php -l` or the WordPress test suite; it was
written to be executable but no PHP runtime existed during this build.

---

## 5. Bugs found and fixed during testing

| Bug | How it surfaced | Fix |
| --- | --- | --- |
| `connection_summary()` crashed with `KeyError: 'token'` | Admin-column test: `format_html()` treats the literal `{token}` in the entry template as a format field | Build the table with `mark_safe` + `escape()` instead of `format_html` |
| `429` response fell back to a 302 in the handoff | Rate-limit test: rendering the 429 page raised inside the broad fallback `try` | Rate limiting moved out of the fallback block; only session creation/redirect are guarded |
| Test suite took 209 s | Profiling: each `DownloadQuality` save triggered the live URL-shortener HTTP call | Tests now use a shortener-allowed CDN host (no outbound call); suite dropped to ~37–50 s |
| Expired-session statistics could be incremented repeatedly | Code review | Conditional `UPDATE ... WHERE status='ready'` (`mark_expired()`), statistics only on the transition |
| Blog page URL was rebuilt incorrectly on sub-directory installs | Code review (`home_url($path)` duplicates the sub-directory) | Rebuild from the submitted scheme/host/port/path and require the same host |
| ZIP written with backslash paths | ZIP inspection | Python packaging script with explicit forward-slash arcnames + automatic verification |
| `403 CSRF verification failed — Origin checking failed - null` when clicking **Generate API key** / **Enable generator** | Reported from a real admin session: the browser sent `Origin: null` because the page was submitted from an opaque-origin context (sandboxed hosting-panel preview iframe / in-app browser). Django rejects the origin *before* checking the token | Added `OpaqueOriginCsrfCompatMiddleware`: only an explicit `null` origin is rewritten to the site's own origin, only for Control Center / private-admin POSTs; the CSRF cookie + token are still verified, foreign origins are still rejected and public views are untouched. Six tests cover the behaviour (see `OpaqueOriginCsrfTests`) |

---

## 6. Part F scenario matrix

Legend: **Auto** = asserted by an executed automated test; **Manual** = requires a
live WordPress + browser (not available here).

| # | Scenario | Automated evidence | Manual check | Status |
| --- | --- | --- | --- | --- |
| 1 | Valid NZDWorld download request | `HandoffTests.test_enabled_handoff_redirects_to_wordpress_with_an_opaque_token`, `SessionLifecycleTests.test_redeem_after_the_countdown_releases_the_destination_once` | Click a movie download button; expect entry URL → random article → countdown → file | Django layer: **passed**; E2E: **manual** |
| 2 | Invalid API key | `ApiAuthenticationTests.test_invalid_api_key_is_rejected_and_audited`, `test_revoked_key_is_rejected` | Put a wrong key in the plugin, Save & Test → error + friendly message | Django layer: **passed**; WP UI: **manual** |
| 3 | Expired session | `SessionLifecycleTests.test_expired_session_cannot_be_validated_or_redeemed` | Load the entry URL, wait past the session lifetime, click Proceed | Django layer: **passed**; WP: **manual** |
| 4 | Reused session token | `test_redeem_after_the_countdown_releases_the_destination_once` (second redeem 409), `test_entry_route_style_revalidation_after_redemption_is_refused` | Reload the entry URL after a successful download | Django layer: **passed**; WP: **manual** |
| 5 | Visitor opens a job article directly | (WordPress-only: no `nzd_gen`, so `detect_session()` never sets a session) | Open an article with no query string → no popup, no request to NZDWorld | **Manual** |
| 6 | Random article selection from eligible published posts | (WordPress-only `WP_Query`: `post_status=publish`, `has_password=false`, optional `category__in`) | Set one category; request several handoffs; confirm only published posts from that category appear | **Manual** |
| 7 | Countdown configuration changes | `test_countdown_is_clamped_to_the_configured_maximum`, `test_countdown_too_long_for_the_session_lifetime_is_refused`, form test `test_settings_form_rejects_unsafe_values` | Change 5 → 120 s in the plugin, reload entry, observe remaining seconds | Django layer: **passed**; WP form: **manual** |
| 8 | Browser refresh during countdown | `test_validate_is_idempotent_and_does_not_restart_the_countdown` | Refresh mid-countdown: remaining time must continue, no new session row | Django layer: **passed**; browser: **manual** |
| 9 | Proceed button after countdown | `test_redeem_after_the_countdown_releases_the_destination_once` (returns destination, counter +1) | Click Proceed after 0 → file download starts | Django layer: **passed**; browser: **manual** |
| 10 | Redeem before countdown completion | `test_redeem_before_the_countdown_is_refused` (409 + `retry_after`, no destination) | Tamper with JS to call redeem immediately (or use the AJAX endpoint) → “Please wait for the countdown” | Django layer: **passed**; WP endpoint: **manual** |
| 11 | Deleted / unavailable quality record | `test_deleted_quality_record_fails_cleanly`, `test_disabled_downloads_fail_cleanly` | Delete the quality row between handoff and Proceed → clean 410 style error | Django layer: **passed**; WP UI: **manual** |
| 12 | Network / API failure between the sites | (Django side returns controlled JSON; PHP maps `network_error`/`server_error` to a friendly retryable message) | Block outbound HTTPS (hosts file/firewall), then Test Connection and Proceed | **Manual** |
| 13 | Integration disabled on NZDWorld | `DisabledIntegrationTests` (4 tests: original download, revoked key, unconfigured fallback, disabled API) | Disable in the Control Center, click a download button → direct link, unchanged | Django layer: **passed**; E2E: **manual** |
| 14 | Mobile and desktop popup layouts | (CSS only) | Open the popup at 360 px, 768 px and 1440 px widths; check scrolling, focus, tap targets | **Manual** |
| 15 | Multiple visitors generating simultaneously | `test_repeated_clicks_create_independent_single_use_sessions` (two independent sessions redeemed independently) | Two browsers (or two devices) start a download at the same time; each gets its own article/session/download | Django: **partial** (sequential, not truly parallel); real concurrency: **manual** |

---

## 7. What was **not** executed (and why)

| Not executed | Reason | How to run it |
| --- | --- | --- |
| `php -l` on every plugin file | No PHP binary, no network to install one | `for f in $(find nzdworld-link-generator -name '*.php'); do php -l "$f"; done` |
| `php tests/run-tests.php` | Same | `cd wp-content/plugins/nzdworld-link-generator && php tests/run-tests.php` |
| WordPress PHPUnit suite / WP-CLI | No WordPress, no database | `wp plugin activate nzdworld-link-generator`, then a real handoff |
| Browser/visual tests (mobile/desktop) | No browser automation in this environment | Load the popup on a real site, use the checklist in §6 rows 5, 8, 9, 14 |
| True concurrency test (two simultaneous redeems) | Django test client is sequential; no load tool available | `ab`/`hey`/two browsers against the redeem endpoint; expected: one 200, one 409 |
| End-to-end handoff between the two live sites | No WordPress instance and no network | Follow `INSTALLATION-GUIDE.md` §5 |

The automated tests therefore prove the **Django side end-to-end** (handoff, API,
session lifecycle, security rules, admin actions) and the **plugin's logic** by
static analysis, but they do not prove that the plugin behaves correctly inside a
running WordPress installation. That final validation needs a staging WordPress
site.

---

## 8. Coverage summary

| Area | Status |
| --- | --- |
| Django system checks | Passed |
| Django migrations consistency | Passed (`No changes detected`) |
| Feature tests (53) | Passed |
| Whole-project regression suite (329 tests) | Passed, exit code 0 |
| Cross-language signature vector | Passed (both suites share it) |
| WordPress ZIP structure/contents | Passed (verified programmatically) |
| PHP/JS/CSS structural syntax | Passed (tokeniser; not a compiler) |
| Live WordPress behaviour | Not executed — see §7 |
| PHP syntax via `php -l` | Not executed — see §7 |
| Browser/mobile layout | Not executed — see §7 |
