from django import template
from subscriptions.utils import append_vip_token_to_url
import re

register = template.Library()


@register.filter
def tokenize_cdn_links(html_content, vip_token):
    """
    Template filter that parses HTML string content (such as post.content or page.content)
    and automatically appends the signed VIP token to any href pointing to the canonical CDN host or legacy shortlinks.
    """
    if not html_content or not vip_token:
        return html_content

    def replace_link(match):
        prefix = match.group(1)
        url = match.group(2)
        suffix = match.group(3)
        tokenized_url = append_vip_token_to_url(url, vip_token)
        return f"{prefix}{tokenized_url}{suffix}"

    pattern = re.compile(
        r'(href=["\'])(https?://(?:cdn\.nzdworld\.com|cdn\.nzdowlrd\.com|dl\.jaraflix\.com)[^"\']*)(["\'])',
        re.IGNORECASE
    )
    return pattern.sub(replace_link, html_content)
