"""
Models for the optional **External Link Generator** integration.

The feature lets an existing ``/download/quality/<id>/`` click be handed off to
an external WordPress installation that shows a random job article plus a
countdown popup. The plugin only receives an opaque, short-lived, single-use
session token. The real download destination stays on this server until the
visitor finishes the countdown and the WordPress site redeems the session
server-to-server.

Design notes
------------
* API keys are only ever stored as a SHA-256 digest (``api_key_hash``) plus a
  short display prefix. Rotating or revoking the key only touches the hash.
* Download destinations are **never** stored on a session. At redemption the
  current ``DownloadQuality.download_url`` is re-read and the existing VIP/CDN
  token helpers are reused, so a changed or deleted quality record is handled
  by the normal download logic.
* Every session row is bound to the exact ``DownloadQuality`` row that the
  visitor clicked (not just the numeric id) and to the registered WordPress
  installation.
* These models are imported from ``core/models.py`` so Django keeps the app
  label ``core`` (no new app, no change to ``INSTALLED_APPS``).
"""

from urllib.parse import urlsplit

from django.db import models
from django.utils import timezone


class ExternalLinkConfig(models.Model):
    """Singleton configuration + connection state for the integration."""

    SINGLETON_PK = 1

    # --- master switch -----------------------------------------------------
    enabled = models.BooleanField(
        default=False,
        help_text=(
            "When on, /download/quality/<id>/ starts a WordPress handoff. "
            "When off, download links behave exactly as before."
        ),
    )

    # --- API credentials (hash only) --------------------------------------
    api_key_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    api_key_prefix = models.CharField(max_length=24, blank=True, default="", editable=False)
    api_key_created_at = models.DateTimeField(null=True, blank=True, editable=False)
    api_key_revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    api_key_last_used_at = models.DateTimeField(null=True, blank=True, editable=False)

    # --- Registered WordPress installation --------------------------------
    wordpress_site_url = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Registered WordPress installation, e.g. https://blog.example.com",
    )
    wordpress_entry_template = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text=(
            "Handoff URL reported by the plugin, containing {token}. "
            "Example: https://blog.example.com/nzdworld-link/{token}/"
        ),
    )
    domain_locked = models.BooleanField(
        default=True,
        help_text=(
            "Reject API calls from any host other than the registered site. "
            "Uncheck (then clear the site URL) to allow a different installation "
            "to re-register."
        ),
    )

    # --- Session lifetime / countdown policy ------------------------------
    session_ttl_seconds = models.PositiveIntegerField(
        default=900,
        help_text="Lifetime of a generation session (120-3600 seconds).",
    )
    min_countdown_seconds = models.PositiveIntegerField(
        default=5, help_text="Lowest countdown the plugin may request (seconds)."
    )
    max_countdown_seconds = models.PositiveIntegerField(
        default=120, help_text="Highest countdown the plugin may request (seconds)."
    )
    api_clock_skew_seconds = models.PositiveIntegerField(
        default=300, help_text="Accepted clock difference for signed API calls."
    )

    # --- Connection / operation state (written by the API) ----------------
    last_connection_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_connection_ok = models.BooleanField(null=True, blank=True, editable=False)
    last_connection_site_url = models.CharField(
        max_length=300, blank=True, default="", editable=False
    )
    last_connection_plugin_version = models.CharField(
        max_length=32, blank=True, default="", editable=False
    )
    last_connection_ip_hash = models.CharField(
        max_length=64, blank=True, default="", editable=False
    )
    last_connection_error = models.CharField(
        max_length=200, blank=True, default="", editable=False
    )
    last_handoff_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_handoff_error = models.CharField(
        max_length=200, blank=True, default="", editable=False
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "External Link Generator settings"
        verbose_name_plural = "External Link Generator settings"

    def __str__(self):
        state = "enabled" if self.enabled else "disabled"
        return f"External Link Generator ({state})"

    # -- singleton helpers -------------------------------------------------
    def save(self, *args, **kwargs):
        """
        Enforce the single-row design.

        Editing works through the normal admin/Control Center form; accidentally
        creating a second row (e.g. by opening the generic "Add New" URL) is
        refused instead of silently overwriting the live configuration.
        """
        if self._state.adding and type(self).objects.filter(pk=self.SINGLETON_PK).exists():
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied(
                "The External Link Generator settings already exist. "
                "Open /control-center/external-link-generator/ and edit them there."
            )
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        """Return the singleton row, creating a disabled default if missing."""
        config, _created = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return config

    # -- derived state -----------------------------------------------------
    @property
    def has_active_key(self):
        return bool(self.api_key_hash) and self.api_key_revoked_at is None

    @property
    def key_state(self):
        if not self.api_key_hash:
            return "missing"
        if self.api_key_revoked_at is not None:
            return "revoked"
        return "active"

    @property
    def site_host(self):
        return _host_of(self.wordpress_site_url)

    @property
    def is_configured(self):
        return bool(self.wordpress_site_url and self.wordpress_entry_template)

    @property
    def is_operational(self):
        """True only when a handoff can actually be produced."""
        return bool(self.enabled and self.has_active_key and self.is_configured)

    @property
    def blocked_reason(self):
        if not self.enabled:
            return "The integration is disabled."
        if not self.has_active_key:
            return "No active API key. Generate one in the Link Generator panel."
        if not self.wordpress_site_url:
            return "No WordPress site is registered yet."
        if not self.wordpress_entry_template:
            return "The WordPress entry URL is missing (run Test Connection in the plugin)."
        return ""

    def touch_connection(self, *, ok, site_url="", plugin_version="", error="", ip_hash=""):
        """Record the outcome of a connection test (called by the API layer)."""
        now = timezone.now()
        self.last_connection_at = now
        self.last_connection_ok = bool(ok)
        self.last_connection_site_url = (site_url or "")[:300]
        self.last_connection_plugin_version = (plugin_version or "")[:32]
        self.last_connection_error = (error or "")[:200]
        self.last_connection_ip_hash = (ip_hash or "")[:64]
        self.save(
            update_fields=[
                "last_connection_at",
                "last_connection_ok",
                "last_connection_site_url",
                "last_connection_plugin_version",
                "last_connection_error",
                "last_connection_ip_hash",
                "updated_at",
            ]
        )


class ExternalLinkSession(models.Model):
    """One opaque, short-lived, single-use handoff session."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        VALIDATED = "validated", "Validated by WordPress"
        REDEEMED = "redeemed", "Redeemed"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"
        FAILED = "failed", "Failed"

    # Only a digest of the token is stored; the plaintext token is returned to
    # the browser once (inside the redirect) and never persisted.
    token_hash = models.CharField(max_length=64, unique=True, db_index=True, editable=False)
    token_prefix = models.CharField(max_length=12, blank=True, default="", editable=False)

    # The exact quality row the visitor clicked. SET_NULL keeps the session
    # row (and its audit trail) when an editor deletes the quality; redemption
    # then fails cleanly with "download unavailable".
    quality = models.ForeignKey(
        "core.DownloadQuality",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="external_link_sessions",
    )
    quality_label = models.CharField(max_length=200, blank=True, default="", editable=False)
    post_id = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    post_title = models.CharField(max_length=200, blank=True, default="", editable=False)

    # Registered installation this session belongs to (host binding).
    site_url = models.CharField(max_length=300, blank=True, default="", editable=False)

    # Active VIP subscriber at handoff time, if any. The signed CDN token is
    # generated from this id at redemption, so no token is stored.
    vip_user_id = models.PositiveIntegerField(null=True, blank=True, editable=False)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    # Server-side "not before" time: redemption is refused before this moment,
    # independently of whatever the browser countdown shows.
    not_before = models.DateTimeField(null=True, blank=True)
    redeemed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_ip_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    redeemed_ip_hash = models.CharField(max_length=64, blank=True, default="", editable=False)

    class Meta:
        verbose_name = "External link session"
        verbose_name_plural = "External link sessions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="core_els_status_exp_idx"),
            models.Index(fields=["site_url", "created_at"], name="core_els_site_idx"),
        ]

    def __str__(self):
        return f"{self.token_prefix}… → {self.quality_label or 'unknown quality'}"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    @property
    def is_redeemed(self):
        return self.redeemed_at is not None

    @property
    def is_redeemable(self):
        now = timezone.now()
        return (
            self.redeemed_at is None
            and self.expires_at > now
            and self.not_before is not None
            and self.not_before <= now
        )


class ExternalLinkEvent(models.Model):
    """Audit trail. Never contains tokens, API keys or download URLs."""

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"

    event = models.CharField(max_length=40, db_index=True)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.INFO)
    message = models.CharField(max_length=300, blank=True, default="")
    session = models.ForeignKey(
        ExternalLinkSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    api_key_prefix = models.CharField(max_length=24, blank=True, default="")
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "External link event"
        verbose_name_plural = "External link events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event", "created_at"], name="core_ele_event_idx"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.event}"


class ExternalLinkNonce(models.Model):
    """Short-lived nonces used to reject replayed signed API requests."""

    nonce = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "External link request nonce"
        verbose_name_plural = "External link request nonces"
        ordering = ["-created_at"]

    def __str__(self):
        return self.nonce[:12]


def _host_of(url):
    """Normalised lowercase host for a URL-ish string ('' when invalid)."""
    try:
        host = urlsplit((url or "").strip()).hostname or ""
    except ValueError:
        return ""
    return host.lower().strip(".")
