"""
Django admin registrations for the External Link Generator integration.

The Control Center page at ``/control-center/external-link-generator/`` is the
primary operator screen (it has one-click key/domain actions); these ModelAdmin
classes keep the private Django admin working, add the same actions, and make the
session/event audit trail browsable.
"""

from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import escape, format_html, mark_safe

from . import external_link_service as service
from .external_link_forms import ExternalLinkConfigForm
from .external_link_models import (
    ExternalLinkConfig,
    ExternalLinkEvent,
    ExternalLinkNonce,
    ExternalLinkSession,
)


def _badge(text, colour, background):
    return format_html(
        '<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
        'font-size:11px;font-weight:600;color:{};background:{};white-space:nowrap">{}</span>',
        colour,
        background,
        text,
    )


@admin.register(ExternalLinkConfig)
class ExternalLinkConfigAdmin(admin.ModelAdmin):
    form = ExternalLinkConfigForm

    list_display = (
        "generator_state",
        "key_badge",
        "wordpress_site_url",
        "connection_badge",
        "session_counts",
    )
    actions = (
        "action_generate_api_key",
        "action_revoke_api_key",
        "action_enable_integration",
        "action_disable_integration",
        "action_reset_domain",
    )
    readonly_fields = (
        "api_key_fingerprint",
        "api_key_last_used_at",
        "connection_summary",
        "operator_panel",
        "statistics_summary",
    )
    fieldsets = (
        (
            "Integration",
            {
                "fields": ("enabled",),
                "description": (
                    "While disabled, /download/quality/<id>/ behaves exactly as before. "
                    "While enabled and configured, it hands the visitor to WordPress."
                ),
            },
        ),
        (
            "Connected WordPress installation",
            {"fields": ("wordpress_site_url", "wordpress_entry_template", "domain_locked", "connection_summary")},
        ),
        (
            "API key",
            {
                "fields": ("api_key_fingerprint", "api_key_last_used_at", "operator_panel"),
                "description": (
                    "Keys are generated and rotated with the actions on the list page "
                    "(or from the Link Generator screen in the Control Center). Only a "
                    "hash is stored; the plaintext is displayed once."
                ),
            },
        ),
        (
            "Session policy",
            {
                "fields": (
                    "session_ttl_seconds",
                    "min_countdown_seconds",
                    "max_countdown_seconds",
                    "api_clock_skew_seconds",
                )
            },
        ),
        ("Statistics", {"fields": ("statistics_summary",)}),
    )

    # -- permissions -------------------------------------------------------
    def has_add_permission(self, request):
        return not ExternalLinkConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    # -- list columns ------------------------------------------------------
    @admin.display(description="Generator")
    def generator_state(self, obj):
        if obj.enabled:
            return _badge("Enabled", "#065f46", "#d1fae5")
        return _badge("Disabled", "#6b7280", "#f3f4f6")

    @admin.display(description="API key")
    def key_badge(self, obj):
        if obj.key_state == "active":
            return _badge(f"Active · {obj.api_key_prefix}…", "#065f46", "#d1fae5")
        if obj.key_state == "revoked":
            return _badge("Revoked", "#b91c1c", "#fee2e2")
        return _badge("Not generated", "#92400e", "#fef3c7")

    @admin.display(description="Connection")
    def connection_badge(self, obj):
        if obj.last_connection_ok is True:
            return _badge("Connected", "#065f46", "#d1fae5")
        if obj.last_connection_ok is False:
            return _badge("Failed", "#b91c1c", "#fee2e2")
        return _badge("Never tested", "#6b7280", "#f3f4f6")

    @admin.display(description="Sessions (redeemed / total)")
    def session_counts(self, obj):
        stats = service.statistics()
        return f"{stats['sessions_redeemed']} / {stats['sessions_total']}"

    # -- read-only panels --------------------------------------------------
    @admin.display(description="Key fingerprint")
    def api_key_fingerprint(self, obj):
        if not obj.api_key_prefix:
            return "No key has been generated yet."
        state = {"active": "active", "revoked": "revoked", "missing": "missing"}[obj.key_state]
        created = obj.api_key_created_at.strftime("%Y-%m-%d %H:%M") if obj.api_key_created_at else "—"
        return format_html(
            "<code>{}</code>… <em>({})</em><br>Issued: {}<br>Revoked: {}",
            obj.api_key_prefix,
            state,
            created,
            obj.api_key_revoked_at.strftime("%Y-%m-%d %H:%M") if obj.api_key_revoked_at else "—",
        )

    @admin.display(description="Connection status")
    def connection_summary(self, obj):
        rows = [
            ("Registered site", obj.wordpress_site_url or "—"),
            ("Entry URL template", obj.wordpress_entry_template or "—"),
            (
                "Last test",
                obj.last_connection_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                if obj.last_connection_at
                else "never",
            ),
            ("Last test result", "success" if obj.last_connection_ok else ("failed" if obj.last_connection_ok is False else "—")),
            ("Plugin version", obj.last_connection_plugin_version or "—"),
            ("Last error", obj.last_connection_error or "—"),
            (
                "Last handoff",
                obj.last_handoff_at.strftime("%Y-%m-%d %H:%M:%S UTC") if obj.last_handoff_at else "never",
            ),
            ("Last handoff problem", obj.last_handoff_error or "—"),
        ]
        # Built with mark_safe + escape() rather than format_html(): the entry
        # template contains a literal "{token}" placeholder that str.format()
        # would try to interpret.
        cells = "".join(
            "<tr><th style='text-align:left;padding:2px 12px 2px 0;font-weight:600'>{}</th>"
            "<td style='padding:2px 0'>{}</td></tr>".format(escape(label), escape(str(value)))
            for label, value in rows
        )
        return mark_safe("<table style='border-collapse:collapse'>" + cells + "</table>")

    @admin.display(description="Operator panel")
    def operator_panel(self, obj):
        try:
            url = reverse("controlcenter:external_link_generator")
        except Exception:  # pragma: no cover - control center always installed here
            url = "/control-center/external-link-generator/"
        if obj.blocked_reason:
            return format_html(
                "<p><strong>Handoff inactive:</strong> {}</p>"
                "<p><a class='button' href='{}'>Open the Link Generator screen</a></p>",
                obj.blocked_reason,
                url,
            )
        return format_html(
            "<p><strong>Ready.</strong> The WordPress handoff is active.</p>"
            "<p><a class='button' href='{}'>Open the Link Generator screen</a></p>",
            url,
        )

    @admin.display(description="Statistics")
    def statistics_summary(self, obj):
        stats = service.statistics()
        return format_html(
            "<table style='border-collapse:collapse'>"
            "<tr><th style='text-align:left;padding:2px 12px 2px 0'>Sessions created</th><td>{}</td></tr>"
            "<tr><th style='text-align:left;padding:2px 12px 2px 0'>Redeemed (downloads)</th><td>{}</td></tr>"
            "<tr><th style='text-align:left;padding:2px 12px 2px 0'>Expired unredeemed</th><td>{}</td></tr>"
            "<tr><th style='text-align:left;padding:2px 12px 2px 0'>Still pending</th><td>{}</td></tr>"
            "<tr><th style='text-align:left;padding:2px 12px 2px 0'>Last {} days</th><td>{} created / {} redeemed</td></tr>"
            "</table>",
            stats["sessions_total"],
            stats["sessions_redeemed"],
            stats["sessions_expired"],
            stats["sessions_pending"],
            stats["window_days"],
            stats["sessions_recent"],
            stats["sessions_recent_redeemed"],
        )

    # -- actions -----------------------------------------------------------
    @admin.action(description="Generate / rotate the API key (plaintext shown once)")
    def action_generate_api_key(self, request, queryset):
        config = queryset.first() or ExternalLinkConfig.load()
        raw_key = service.issue_api_key(config)
        self.message_user(
            request,
            f"New API key generated. Copy it into WordPress now — it will not be shown again: {raw_key}",
            level=messages.WARNING,
        )

    @admin.action(description="Revoke the API key")
    def action_revoke_api_key(self, request, queryset):
        config = queryset.first() or ExternalLinkConfig.load()
        if service.revoke_api_key(config):
            self.message_user(
                request,
                "API key revoked. Every WordPress request will now be rejected until a new key is issued.",
                level=messages.WARNING,
            )
        else:
            self.message_user(request, "There is no API key to revoke.", level=messages.INFO)

    @admin.action(description="Enable the generator")
    def action_enable_integration(self, request, queryset):
        config = queryset.first() or ExternalLinkConfig.load()
        config.enabled = True
        config.save(update_fields=["enabled", "updated_at"])
        service.log_event("integration_enabled", message="Generator enabled from the Django admin.")
        if not config.is_operational:
            self.message_user(
                request,
                f"Generator enabled, but the handoff is inactive: {config.blocked_reason}",
                level=messages.WARNING,
            )
        else:
            self.message_user(request, "Generator enabled; download buttons now use the WordPress handoff.")

    @admin.action(description="Disable the generator (restore normal downloads)")
    def action_disable_integration(self, request, queryset):
        config = queryset.first() or ExternalLinkConfig.load()
        config.enabled = False
        config.save(update_fields=["enabled", "updated_at"])
        service.log_event("integration_disabled", message="Generator disabled from the Django admin.")
        self.message_user(request, "Generator disabled. /download/quality/<id>/ behaves as before.")

    @admin.action(description="Forget the connected WordPress site (re-register on next test)")
    def action_reset_domain(self, request, queryset):
        config = queryset.first() or ExternalLinkConfig.load()
        service.reset_registered_domain(config, keep_key=True)
        self.message_user(
            request,
            "Registered WordPress site cleared. Run Test Connection in the plugin to re-register it.",
            level=messages.WARNING,
        )


@admin.register(ExternalLinkSession)
class ExternalLinkSessionAdmin(admin.ModelAdmin):
    list_display = (
        "token_prefix",
        "quality_label",
        "status",
        "site_url",
        "created_at",
        "expires_at",
        "redeemed_at",
    )
    list_filter = ("status", "site_url", "created_at")
    search_fields = ("token_prefix", "quality_label", "post_title")
    readonly_fields = (
        "token_prefix",
        "token_hash",
        "quality",
        "quality_label",
        "post_id",
        "post_title",
        "site_url",
        "vip_user_id",
        "status",
        "created_at",
        "expires_at",
        "validated_at",
        "not_before",
        "redeemed_at",
        "created_ip_hash",
        "redeemed_ip_hash",
    )
    actions = ("action_revoke_sessions", "action_expire_sessions")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.action(description="Revoke selected sessions (cannot be redeemed)")
    def action_revoke_sessions(self, request, queryset):
        updated = queryset.filter(redeemed_at__isnull=True).update(
            status=ExternalLinkSession.Status.REVOKED
        )
        service.log_event(
            "sessions_revoked_admin",
            level=ExternalLinkEvent.Level.WARNING,
            message=f"{updated} session(s) revoked by an administrator.",
        )
        self.message_user(request, f"{updated} session(s) revoked.")

    @admin.action(description="Mark selected sessions as expired")
    def action_expire_sessions(self, request, queryset):
        updated = queryset.filter(redeemed_at__isnull=True).update(
            status=ExternalLinkSession.Status.EXPIRED
        )
        self.message_user(request, f"{updated} session(s) marked as expired.")


@admin.register(ExternalLinkEvent)
class ExternalLinkEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event", "level", "message", "api_key_prefix", "session")
    list_filter = ("level", "event", "created_at")
    search_fields = ("event", "message")
    readonly_fields = (
        "created_at",
        "event",
        "level",
        "message",
        "session",
        "api_key_prefix",
        "ip_hash",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(ExternalLinkNonce)
class ExternalLinkNonceAdmin(admin.ModelAdmin):
    """Visible only for troubleshooting replayed-request reports."""

    list_display = ("nonce", "created_at")
    readonly_fields = ("nonce", "created_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

