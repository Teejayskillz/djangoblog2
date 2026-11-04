# NZDWorld External Link Generator — Installation & Setup Guide

This guide installs the optional **External Link Generator** feature on both sides:

* **NZDWorld (Django)** — decides when to hand a download click to WordPress, issues
  one-time tokens and finally releases the real download URL.
* **NZDWorld Link Generator (WordPress plugin)** — verifies the token, shows a
  random published job article with a countdown popup and asks NZDWorld for the
  download after the countdown.

> The feature is **off by default**. Until you enable it, `/download/quality/<id>/`
> behaves exactly as it does today.

---

## 1. What was delivered

| Deliverable | Where |
| --- | --- |
| WordPress plugin ZIP (installable) | `nzdworld-link-generator.zip` |
| WordPress plugin source | `wordpress-plugin/nzdworld-link-generator/` |
| Django changes (applied to this project) | `nzdworld-django-integration/external-link-generator.patch` |
| Django change summary | `nzdworld-django-integration/README.md`, `FILES.md` |
| API specification | `API-DOCUMENTATION.md` |
| Test evidence | `TEST-REPORT.md` |
| This guide | `INSTALLATION-GUIDE.md` |

The Django changes in this repository were made on the git branch
`feature/external-link-generator`; the untouched starting point is preserved on
`backup/pre-external-link-generator`.

---

## 2. Requirements

### NZDWorld (Django)

* The existing project (Django 4.2+ / 5.0, Python 3.10+).
* Database migrations applied (the feature adds migration `core.0017`).
* The site served over **HTTPS** (the API is HTTPS-only by design).
* Outbound HTTPS from the server is **not** required — Django only answers
  requests; WordPress is the client.

### WordPress

* WordPress 5.8 or newer, PHP 7.4 or newer.
* PHP extensions: **openssl** (required — used to encrypt session tokens at
  rest), `curl` or HTTPS stream wrappers for `wp_remote_post`, `json`.
* Outbound HTTPS from WordPress to the NZDWorld domain (for signed API calls).
* Pretty permalinks recommended (`Settings → Permalinks → Post name`), but the
  plugin also works with plain permalinks through a query-string entry URL.
* No page builder, no paid plugin, no JavaScript framework.

---

## 3. Install the Django side

### 3.1 Back up / check out the branch

The working copy already contains the changes. If you deploy from a repository:

```bash
git switch feature/external-link-generator     # contains the feature
# or apply the patch to your own branch:
git apply nzdworld-django-integration/external-link-generator.patch
```

### 3.2 Files that were added

| File | Purpose |
| --- | --- |
| `core/external_link_models.py` | `ExternalLinkConfig` (singleton), `ExternalLinkSession`, `ExternalLinkEvent`, `ExternalLinkNonce` |
| `core/external_link_service.py` | API-key lifecycle, signed-request authentication, session creation/validation/redemption, rate limiting, audit log, statistics |
| `core/external_link_views.py` | The JSON API endpoints (`connection-test`, `sessions/create`, `sessions/validate`, `sessions/redeem`) |
| `core/external_link_urls.py` | URLconf mounted at `/api/external-link/` |
| `core/external_link_forms.py` | Validation for the admin settings (URLs, TTL, countdown, clock skew) |
| `core/external_link_admin.py` | Django-admin registration, read-only session/event views, key/domain actions |
| `core/migrations/0017_external_link_generator.py` | Database migration |
| `core/test_external_links.py` | 47 automated tests |
| `controlcenter/external_link_views.py` + template | The operator screen at `/control-center/external-link-generator/` |

### 3.3 Files that were modified (small, additive changes)

| File | Change |
| --- | --- |
| `blog_project/settings.py` | `EXTERNAL_LINK_REQUIRE_HTTPS`, `EXTERNAL_LINK_API_BASE`, two rate-limit scopes |
| `blog_project/urls.py` | Mounts `/api/external-link/` **before** `core.urls` |
| `core/models.py` | Imports the new models into the `core` app (no new app, no INSTALLED_APPS change) |
| `core/admin.py` | Imports the new ModelAdmins |
| `core/views.py` | ~10 lines inside `download_quality()` that call `start_handoff()`; returns `None` unless enabled **and** fully configured, so the original path is untouched otherwise |
| `controlcenter/urls.py` | Adds the Link Generator screen before the generic model routes |
| `controlcenter/templates/controlcenter/base.html` | One sidebar item ("Link Generator") |

### 3.4 Apply and verify

```bash
python manage.py migrate            # creates the four new tables
python manage.py check              # must print: no issues
python manage.py test core.test_external_links    # 47 tests
```

Restart the application so the new code is live:

* cPanel / Passenger: `touch tmp/restart.txt`, or restart the Python app in
  *Setup Python App*.
* The `/api/external-link/` index should then answer with HTTP 200:

```bash
curl -s https://nzdworld.com/api/external-link/ | head
```

No static files need collecting for this feature.

### 3.5 Configure it in NZDWorld

1. Log in to the Control Center and open **Link Generator**
   (`/control-center/external-link-generator/`, also linked from the sidebar).
   The same settings are available in the private Django admin
   (`/django-admin-backend-internal/core/externallinkconfig/`).
2. Click **Generate API key**. The plaintext key is displayed **once** — copy it
   now; only a SHA-256 digest is stored.
3. Leave **Enabled** off until WordPress is configured (or enable it now; the
   handoff only starts when a site is connected).
4. Optional policy fields:
   * **Session lifetime** — 120–3600 s (default 900). Must exceed the maximum
     countdown by at least 30 s.
   * **Min / max countdown** — the plugin may request 5–120 s.
   * **Domain lock** — when on, only the registered WordPress host may call the
     API (leave it on).
   * **Entry URL template** — filled automatically by the plugin's connection
     test; shown read-only in the panel.
5. Click **Enable generator** once the WordPress side is connected.
6. Keep **Revoke key**, **Rotate key**, and **Forget connected site** at hand for
   incident response. Connection status (`Connected / Failed / Last successful
   test`), the last handoff problem and statistics are all shown on the same
   screen.

---

## 4. Install the WordPress plugin

### 4.1 Upload the ZIP

1. WordPress admin → **Plugins → Add New → Upload Plugin**.
2. Choose `nzdworld-link-generator.zip` → **Install Now** → **Activate**.
3. A new menu item **NZDWorld Link Generator** appears in the admin sidebar.

The archive contains a single plugin folder:

```
nzdworld-link-generator.zip
└── nzdworld-link-generator/
    ├── nzdworld-link-generator.php   ← plugin header
    ├── uninstall.php
    ├── readme.txt
    ├── includes/                     ← 9 PHP classes
    ├── templates/                    ← admin screen + popup markup
    ├── assets/                       ← generator.css, generator.js
    └── tests/run-tests.php           ← optional logic tests
```

### 4.2 Configure it

Open **NZDWorld Link Generator** and fill in:

| Setting | Example | Notes |
| --- | --- | --- |
| Enable generator | ✓ | Must be on for handoffs to be accepted |
| NZDWorld website URL | `https://nzdworld.com` | HTTPS only |
| API key | `nzd_live_…` | Paste the key shown once in step 3.5; the field is masked. **Leaving it blank keeps the stored key.** |
| Blog page link | `https://blog.example.com/jobs/` | Your jobs/blog page **on this site**. Used as the popup page when random article routing is off, and to verify the layout |
| Countdown duration | `20` | 5–120 seconds |
| Random article routing | ✓ | On: random published post. Off: the blog page link above |
| Use all published posts | ✓ | Uncheck to restrict to the selected categories |
| Popup heading / description / button text | — | Defaults: "Generating your download link", "Please wait while we prepare your link.", "Click to Proceed" |
| Popup logo URL | optional | Absolute HTTPS image URL; the built-in icon is used when empty |
| Session expiration | `900` | Capped by the value NZDWorld reports (shown next to the field) |

Click **Save & Test Connection**. Expected result:

* the status changes to **Connected** (or "connected, generator disabled on
  NZDWorld" if you have not enabled it there yet),
* NZDWorld registers this site and the entry URL automatically,
* "Last successful connection" is filled in.

Then enable the generator on the NZDWorld side (step 3.5) and click
**Save & Test Connection** again if the status was "disabled".

### 4.3 Permalinks and caching

* Pretty permalinks: entry URL is
  `https://blog.example.com/nzdworld-link/<token>/`.
* Plain permalinks: the plugin reports
  `https://blog.example.com/?nzdworld_token=<token>` instead — nothing to do.
* If you change permalinks or move the site, run **Save & Test Connection**
  again so NZDWorld stores the new entry URL.
* Page-cache plugins: pages containing the popup send `nocache_headers()` and set
  `DONOTCACHEPAGE`. The handoff URL itself is unique per token, so it is never
  cached. If you use a full-page cache, exclude URLs with `nzd_gen=` from
  caching.

---

## 5. End-to-end verification

1. Open any movie page on NZDWorld and click a download-quality button.
2. The browser should **not** go straight to the file host. It should briefly go
   to `https://blog.example.com/nzdworld-link/<opaque-token>/`, then to a random
   published job article with `?nzd_gen=<opaque-id>` and the popup open.
3. The countdown counts down; refreshing the article keeps the remaining time.
4. After zero, the button appears. Click it: the browser continues to the
   original download host.
5. Clicking the same entry URL again shows "this download link has already been
   used".
6. In NZDWorld → Link Generator, the statistics show one session and one
   redemption, and the download counter on the movie page increased by one.

---

## 6. Day-2 operations

| Task | Where | Notes |
| --- | --- | --- |
| Rotate the API key | Link Generator → **Rotate API key** → paste into WordPress | Old key stops working immediately |
| Revoke access | **Revoke key** or **Forget connected site** | API returns 401/403; downloads fall back to the direct link |
| Disable the feature | **Disable generator** | `/download/quality/<id>/` returns to its original behaviour instantly |
| Inspect sessions / events | Link Generator page; Django admin → External link sessions / events | Destinations are never stored |
| Clean up abandoned sessions | Hourly WP-Cron (`nzd_lg_cleanup`) and the "Mark abandoned sessions as expired" button | Nothing to configure |
| Change session lifetime / countdown policy | Link Generator settings | The plugin clamps itself to the advertised maximum |
| Move WordPress to a new domain | Plugin: update URLs and re-test; NZDWorld: **Forget connected site** if the host changed | Domain lock prevents silent re-binding |
| Emergency stop | Disable in NZDWorld **and** in the plugin | Either side alone stops the handoff |

Download counters: while the generator is active, `download_count` is incremented
when a session is **redeemed** (the visitor really continues to the file), not on
the button click. With the generator disabled the counter behaves exactly as
before.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| NZDWorld API answers `insecure_transport` | Request did not arrive over TLS (proxy not detected) | Serve the site over HTTPS; if Passenger terminates TLS and Django cannot see it, add the proxy to `TRUSTED_PROXY_IPS` or (temporarily) set `EXTERNAL_LINK_REQUIRE_HTTPS=false` |
| Test Connection: `unauthorized` | Wrong/rotated/revoked key | Generate a new key and paste it again |
| Test Connection: `domain_mismatch` | Another host already registered | NZDWorld → Link Generator → **Forget connected site**, then test again |
| Test Connection: `stale_request` / `invalid_signature` | Server clocks differ by more than 5 minutes | Fix NTP on either server |
| Test Connection: `network_error` | WordPress cannot reach NZDWorld | Check outbound HTTPS, DNS, firewall, TLS certificate |
| `invalid_entry_url` | Entry URL not HTTPS or on another host | Re-save settings; the plugin computes it from `home_url()` |
| Popup never appears | Session invalid/expired, wrong article, or the visitor cookie was lost | Check the WP session table and NZDWorld events; ensure the article was reached via the handoff |
| "PHP OpenSSL is missing" notice | PHP `openssl` extension unavailable | Enable `openssl` on the host |
| "This site is not using HTTPS" | WordPress `home_url()` is `http://` | Enable TLS and update the WordPress address |
| Handoff silently falls back to the direct link | Integration enabled but not operational | Link Generator page shows the exact reason ("Last handoff problem") |

---

## 8. Rollback

1. **Disable** the generator (NZDWorld and/or plugin). Downloads are back to the
   original behaviour immediately; no data is lost.
2. Optional code rollback on Django:

   ```bash
   git switch main            # or: git apply -R nzdworld-django-integration/external-link-generator.patch
   python manage.py migrate core 0016
   ```

3. WordPress: deactivate the plugin (settings and sessions are kept) or
   uninstall it (the uninstall routine removes its options and table).

---

## 9. Security notes

* The API key is stored as a SHA-256 digest on NZDWorld and only as a WordPress
  option on the WordPress side — never in page source, JavaScript, cookies or
  URLs.
* Everything is HTTPS-only; the API also refuses non-signed or replayed
  requests (timestamp window + single-use nonce).
* Session tokens are opaque (`secrets.token_urlsafe(32)`), short-lived,
  single-use and stored hashed on NZDWorld; WordPress stores them encrypted with
  AES-256-GCM using its own salts.
* The real download URL only leaves NZDWorld through the authenticated
  `sessions/redeem/` endpoint, after the server-side `not_before` time and after
  an atomic `UPDATE ... WHERE redeemed_at IS NULL`.
* WordPress admin actions require `manage_options` + nonces; frontend redemption
  requires a nonce and a session bound to the visitor's cookie/network.
* The handoff redirect sends `Referrer-Policy: no-referrer` so the token cannot
  leak through referrer headers.

ok