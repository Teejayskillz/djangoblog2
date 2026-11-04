# NZDWorld — Django side of the External Link Generator

This folder documents the changes that were applied directly to the NZDWorld
Django project (the working copy on branch `feature/external-link-generator`).
The untouched starting point is branch `backup/pre-external-link-generator`.

* **`external-link-generator.patch`** — complete diff (18 files, +3 638 / −1
  lines) including the new migration, the CSRF compatibility middleware and the
  automated tests.
* **`FILES.md`** — every changed/added file and why.

---

## Apply

From the project root (on your own branch, with a clean tree):

```bash
git apply --check nzdworld-django-integration/external-link-generator.patch
git apply         nzdworld-django-integration/external-link-generator.patch
python manage.py migrate
python manage.py check
```

If you prefer to copy files instead of patching, the patch is a plain unified
diff; every file under `core/` and `controlcenter/` in it is new except
`core/models.py`, `core/admin.py`, `core/views.py`, `controlcenter/urls.py`,
`controlcenter/templates/controlcenter/base.html` and the two
`blog_project/*.py` files, which only receive small additive hunks.

## Configure

1. Restart the Python app (Passenger: `touch tmp/restart.txt`).
2. Control Center → **Link Generator** (`/control-center/external-link-generator/`).
3. **Generate API key** (shown once), copy it into the WordPress plugin.
4. WordPress → plugin → **Save & Test Connection**; the registered site and entry
   URL appear on the NZDWorld screen automatically.
5. **Enable generator**.

The same settings are editable in the private Django admin
(`/django-admin-backend-internal/core/externallinkconfig/`) including bulk
actions to generate/rotate/revoke keys, enable/disable and forget the connected
site. Sessions and audit events are browsable there read-only.

## Behaviour when disabled

`download_quality()` in `core/views.py` calls `start_handoff()`, which returns
`None` unless the feature is **enabled** *and* an API key, a registered site and
an entry URL exist. In every other case the original code path runs unchanged
(same 302, same counter increment, same VIP gate). Premium downloads without an
active VIP subscription never start a handoff; they keep redirecting to the plans
page.

When the handoff is active the `download_count` is incremented at redemption
instead of at the click, because that is when the visitor really continues to the
file.

## Tests

```bash
python manage.py test core.test_external_links -v 2   # 53 tests
python manage.py test                                 # full project suite
```

The feature tests cover the handoff, signed API (bad key, tampering, replay,
clock skew), connection registration, domain binding, countdown clamping and
`not_before`, early redemption, double redemption, expiry, deleted/disabled
quality rows, VIP token reuse, rate limiting, admin actions and the
"no destination before redemption" rule. See `../TEST-REPORT.md`.

## Rollback

```bash
git switch main
# or
git apply -R nzdworld-django-integration/external-link-generator.patch
python manage.py migrate core 0016
```

Disabling the feature (step 5 above) is enough to restore the original download
behaviour without touching code.
