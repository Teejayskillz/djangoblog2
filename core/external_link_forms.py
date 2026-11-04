"""
Admin form for the External Link Generator configuration.

Shared by the Django admin, the Control Center model pages and the dedicated
Link Generator screen so validation rules only exist once.
"""

from django import forms

from . import external_link_service as service
from .external_link_models import ExternalLinkConfig

EDITABLE_FIELDS = (
    "enabled",
    "wordpress_site_url",
    "wordpress_entry_template",
    "domain_locked",
    "session_ttl_seconds",
    "min_countdown_seconds",
    "max_countdown_seconds",
    "api_clock_skew_seconds",
)

MIN_TTL = 120
MAX_TTL = 3600
MIN_COUNTDOWN = 1
MAX_COUNTDOWN = 600


class ExternalLinkConfigForm(forms.ModelForm):
    class Meta:
        model = ExternalLinkConfig
        fields = EDITABLE_FIELDS
        widgets = {
            "wordpress_site_url": forms.URLInput(
                attrs={"placeholder": "https://blog.example.com", "size": 60}
            ),
            "wordpress_entry_template": forms.TextInput(
                attrs={"placeholder": "https://blog.example.com/nzdworld-link/{token}/", "size": 80}
            ),
        }

    def clean_wordpress_site_url(self):
        raw = (self.cleaned_data.get("wordpress_site_url") or "").strip()
        if not raw:
            return ""
        normalized = service.normalize_site_url(raw)
        if not normalized:
            raise forms.ValidationError(
                "Enter an absolute HTTPS URL without credentials, query string or "
                "fragment, for example https://blog.example.com"
            )
        return normalized

    def clean(self):
        cleaned = super().clean()
        site_url = cleaned.get("wordpress_site_url") or ""
        template = (cleaned.get("wordpress_entry_template") or "").strip()

        if template:
            if not site_url:
                self.add_error(
                    "wordpress_entry_template",
                    "Register the WordPress site URL before setting the entry URL.",
                )
            else:
                validated = service.validate_entry_template(template, site_url)
                if not validated:
                    self.add_error(
                        "wordpress_entry_template",
                        "Must be an HTTPS URL on the registered site containing exactly "
                        "one {token} placeholder, e.g. "
                        "https://blog.example.com/nzdworld-link/{token}/",
                    )
                else:
                    cleaned["wordpress_entry_template"] = validated

        ttl = cleaned.get("session_ttl_seconds") or 0
        minimum = cleaned.get("min_countdown_seconds") or 0
        maximum = cleaned.get("max_countdown_seconds") or 0
        skew = cleaned.get("api_clock_skew_seconds") or 0

        if not (MIN_TTL <= ttl <= MAX_TTL):
            self.add_error(
                "session_ttl_seconds",
                f"Session lifetime must be between {MIN_TTL} and {MAX_TTL} seconds.",
            )
        if minimum < MIN_COUNTDOWN:
            self.add_error(
                "min_countdown_seconds", f"Minimum countdown must be at least {MIN_COUNTDOWN}s."
            )
        if maximum > MAX_COUNTDOWN:
            self.add_error(
                "max_countdown_seconds", f"Maximum countdown must be {MAX_COUNTDOWN}s or less."
            )
        if maximum and minimum and maximum < minimum:
            self.add_error(
                "max_countdown_seconds",
                "Maximum countdown must be greater than or equal to the minimum.",
            )
        if ttl and maximum and ttl < maximum + 30:
            self.add_error(
                "session_ttl_seconds",
                "Session lifetime must exceed the maximum countdown by at least 30 seconds "
                f"(at least {maximum + 30}s).",
            )
        if not (30 <= skew <= 3600):
            self.add_error(
                "api_clock_skew_seconds",
                "Accepted clock difference must be between 30 and 3600 seconds.",
            )

        return cleaned
