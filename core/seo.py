"""
SEO helpers for NZDWorld post pages.

Everything here is derived from real content on the post (never invented), and
URLs are built from settings.SITE_URL so the canonical host/protocol stays
consistent regardless of the host used to reach the site (www vs non-www).

Structured data notes (Google Search Central):
* Article/BlogPosting -- supported structured data for editorial pages
  https://developers.google.com/search/docs/appearance/structured-data/article
* BreadcrumbList -- supported:
  https://developers.google.com/search/docs/appearance/structured-data/breadcrumb
* Comments are NOT marked up: Google has no comment rich result for an article
  page (DiscussionForumPosting is for forum pages, not blog comments), and
  marking up non-primary content is discouraged by the structured data policies.
"""

import json
import re

from django.conf import settings
from django.utils.html import strip_tags

MAX_DESCRIPTION_LENGTH = 160
MAX_HEADLINE_LENGTH = 110

DEFAULT_DESCRIPTION = (
    'NZDWorld - movies, series and sports streaming updates with download '
    'links, subtitles and VIP ad-free access.'
)

_TEMPLATE_TAG_RE = re.compile(r'\{\{.*?\}\}|\{%.*?%\}')
_WHITESPACE_RE = re.compile(r'\s+')


def site_root():
    """Canonical site root without a trailing slash (e.g. https://nzdworld.com)."""
    return (getattr(settings, 'SITE_URL', '') or '').rstrip('/')


def absolute_url(path):
    """Return `path` on the canonical site host."""
    if not path:
        return ''
    if path.startswith(('http://', 'https://')):
        return path
    root = site_root()
    if not path.startswith('/'):
        path = '/' + path
    return f'{root}{path}' if root else path


def default_social_image():
    static_url = getattr(settings, 'STATIC_URL', '/static/')
    return absolute_url(f'{static_url.rstrip("/")}/images/default_thumbnail.jpg')


def _clean_text(value):
    text = _TEMPLATE_TAG_RE.sub(' ', value or '')
    text = strip_tags(text)
    return _WHITESPACE_RE.sub(' ', text).strip()


def truncate(text, max_length):
    """Trim to `max_length` on a word boundary, without inventing content."""
    text = (text or '').strip()
    if len(text) <= max_length:
        return text
    cut = text[:max_length + 1]
    if ' ' in cut[max_length // 2:]:
        cut = cut[:cut.rfind(' ')]
    else:
        cut = cut[:max_length]
    return cut.rstrip(' ,;:-') + '…'


def post_headline(post):
    headline = _clean_text(post.seo_title) or _clean_text(post.title)
    return truncate(headline, MAX_HEADLINE_LENGTH)


def post_meta_description(post, max_length=MAX_DESCRIPTION_LENGTH):
    """
    Content-specific description: excerpt first, then the article body, then a
    site-wide fallback. Never empty, never duplicated across posts that have
    different content.
    """
    for candidate in (_clean_text(post.excerpt), _clean_text(post.content)):
        if len(candidate) >= 25:
            return truncate(candidate, max_length)
    return DEFAULT_DESCRIPTION


def post_author_name(post):
    author = getattr(post, 'author', None)
    if not author:
        return ''
    return author.get_full_name() or author.username


def build_article_json_ld(post, canonical_url, image_url):
    """BlogPosting JSON-LD for a single published post."""
    root = site_root()
    data = {
        '@context': 'https://schema.org',
        '@type': 'BlogPosting',
        'mainEntityOfPage': {'@type': 'WebPage', '@id': canonical_url},
        'headline': post_headline(post),
        'description': post_meta_description(post, 300),
        'url': canonical_url,
        'datePublished': post.published_date.isoformat() if post.published_date else None,
        'inLanguage': 'en',
    }

    if image_url:
        data['image'] = [image_url]

    author_name = post_author_name(post)
    if author_name:
        # Only the site's own author record; no invented profile URLs.
        data['author'] = {'@type': 'Person', 'name': author_name}

    if post.category:
        data['articleSection'] = post.category.name

    tag_names = [tag.name for tag in post.tags.all()] if hasattr(post, 'tags') else []
    if tag_names:
        data['keywords'] = ', '.join(tag_names)

    if root:
        data['publisher'] = {'@id': f'{root}/#organization'}

    return {key: value for key, value in data.items() if value not in (None, '', [])}


def build_breadcrumb_json_ld(breadcrumbs):
    """
    BreadcrumbList from a list of (name, path|None) tuples. The final entry is
    the current page and omits `item`, as described in Google's breadcrumb docs.
    """
    items = []
    for position, (name, path) in enumerate(breadcrumbs, start=1):
        entry = {'@type': 'ListItem', 'position': position, 'name': name}
        if path:
            entry['item'] = absolute_url(path)
        items.append(entry)
    return {
        '@context': 'https://schema.org',
        '@type': 'BreadcrumbList',
        'itemListElement': items,
    }


def build_site_json_ld():
    """Site-wide WebSite + Organization nodes (referenced by publisher @id)."""
    root = site_root()
    if not root:
        return {}
    organization_id = f'{root}/#organization'
    return {
        '@context': 'https://schema.org',
        '@graph': [
            {
                '@type': 'Organization',
                '@id': organization_id,
                'name': 'NZDWorld',
                'url': f'{root}/',
                'logo': {
                    '@type': 'ImageObject',
                    'url': absolute_url(f'{getattr(settings, "STATIC_URL", "/static/").rstrip("/")}/images/apple-touch-icon.png'),
                },
            },
            {
                '@type': 'WebSite',
                '@id': f'{root}/#website',
                'url': f'{root}/',
                'name': 'NZDWorld',
                'inLanguage': 'en',
                'publisher': {'@id': organization_id},
            },
        ],
    }


def json_ld_text(data):
    """
    Serialise JSON-LD for embedding inside <script type="application/ld+json">.

    '<', '>' and '&' are escaped as JSON unicode escapes so a post title can
    never terminate the script tag early.
    """
    if not data:
        return ''
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return text.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
