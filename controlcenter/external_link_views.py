"""
Control Center screen for the optional External Link Generator integration.

One page to run the whole feature without touching code:

* enable / disable the generator,
* generate, rotate and revoke the API key (plaintext shown exactly once),
* register / forget the connected WordPress installation,
* see connection status, the handoff URL template and basic statistics,
* inspect recent generation sessions and audit events.

The same data is available through the private Django admin; this screen is the
operator-facing one that matches the rest of the site's admin experience.
"""

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.generic import View

from core import external_link_service as service
from core.external_link_forms import ExternalLinkConfigForm
from core.external_link_models import ExternalLinkConfig

from .views import StaffRequiredMixin, get_app_list_context

CHANGE_PERMISSION = "core.change_externallinkconfig"


class ExternalLinkGeneratorView(StaffRequiredMixin, View):
    template_name = "controlcenter/external_link_generator.html"
    active_menu = "external_link_generator"

    # -- helpers -----------------------------------------------------------
    def _allowed(self, request):
        return bool(
            request.user.is_superuser or request.user.has_perm(CHANGE_PERMISSION)
        )

    def _redirect(self):
        return redirect("controlcenter:external_link_generator")

    def _context(self, request, config, form, status=200):
        context = {
            "config": config,
            "form": form,
            "stats": service.statistics(),
            "recent_sessions": service.recent_sessions(15),
            "recent_events": service.recent_events(15),
            "api_base": "/api/external-link",
            "active_menu": self.active_menu,
            "entry_example": "https://your-blog.example/nzdworld-link/{token}/",
            "handoff_preview": (config.wordpress_entry_template or "").replace(
                "{token}", "‹one-time-token›"
            ),
            "now": timezone.now(),
        }
        context.update(get_app_list_context(request))
        return context

    # -- HTTP --------------------------------------------------------------
    def get(self, request, *args, **kwargs):
        if not self._allowed(request):
            return HttpResponseForbidden("Insufficient permissions")

        config = ExternalLinkConfig.load()
        # One-shot plaintext key: displayed after a PRG redirect, then removed.
        new_api_key = request.session.pop("external_link_new_key", "")
        context = self._context(request, config, ExternalLinkConfigForm(instance=config))
        context["new_api_key"] = new_api_key
        response = render(request, self.template_name, context)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response["Referrer-Policy"] = "no-referrer"
        return response

    def post(self, request, *args, **kwargs):
        if not self._allowed(request):
            return HttpResponseForbidden("Insufficient permissions")

        action = (request.POST.get("action") or "").strip()
        config = ExternalLinkConfig.load()

        if action == "save":
            form = ExternalLinkConfigForm(request.POST, instance=config)
            if form.is_valid():
                form.save()
                service.log_event(
                    "settings_updated",
                    message="Generator settings updated in the Control Center.",
                )
                messages.success(request, "Settings saved.")
                return self._redirect()
            messages.error(request, "Please correct the errors below.")
            return render(request, self.template_name, self._context(request, config, form))

        if action == "generate_key":
            raw_key = service.issue_api_key(config)
            request.session["external_link_new_key"] = raw_key
            request.session.modified = True
            messages.warning(
                request,
                "A new API key was generated. Paste it into the WordPress plugin now: "
                "it is shown only once.",
            )
            return self._redirect()

        if action == "revoke_key":
            if service.revoke_api_key(config):
                messages.warning(
                    request,
                    "API key revoked. WordPress requests will be rejected until you issue a new key.",
                )
            else:
                messages.info(request, "There is no API key to revoke.")
            return self._redirect()

        if action == "reset_domain":
            service.reset_registered_domain(config, keep_key=True)
            messages.warning(
                request,
                "The registered WordPress site was cleared. Run Test Connection in the plugin "
                "to register it again.",
            )
            return self._redirect()

        if action == "enable":
            config.enabled = True
            config.save(update_fields=["enabled", "updated_at"])
            service.log_event("integration_enabled", message="Generator enabled from the Control Center.")
            if config.is_operational:
                messages.success(
                    request, "Generator enabled. Download buttons now use the WordPress handoff."
                )
            else:
                messages.warning(
                    request,
                    f"Generator enabled, but the handoff is not active yet: {config.blocked_reason}",
                )
            return self._redirect()

        if action == "disable":
            config.enabled = False
            config.save(update_fields=["enabled", "updated_at"])
            service.log_event("integration_disabled", message="Generator disabled from the Control Center.")
            messages.success(
                request,
                "Generator disabled. /download/quality/<id>/ behaves exactly as before.",
            )
            return self._redirect()

        if action == "prune_sessions":
            from core.external_link_models import ExternalLinkSession

            now = timezone.now()
            expired = ExternalLinkSession.objects.filter(
                redeemed_at__isnull=True, expires_at__lte=now
            )
            count = expired.update(status=ExternalLinkSession.Status.EXPIRED)
            messages.success(request, f"{count} abandoned session(s) marked as expired.")
            return self._redirect()

        messages.error(request, "Unknown action.")
        return self._redirect()
