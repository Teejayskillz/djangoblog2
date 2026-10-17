"""
Point the django.contrib.sites row at the canonical domain.

The sitemap framework and django.contrib.sites use Site.domain to build
absolute URLs (XML sitemap <loc> entries, Telegram post links). A fresh
project keeps Django's placeholder row ("example.com"), which would publish
sitemap entries pointing at the wrong host.

Only the placeholder is replaced: a domain the owner already configured is left
untouched.
"""

from urllib.parse import urlparse

from django.conf import settings
from django.db import migrations

PLACEHOLDER_DOMAINS = {'', 'example.com', 'www.example.com', 'localhost', 'testserver'}


def set_canonical_site_domain(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')

    netloc = urlparse(getattr(settings, 'SITE_URL', '') or '').netloc
    if not netloc:
        return

    site = Site.objects.filter(pk=getattr(settings, 'SITE_ID', 1)).first() or Site.objects.first()
    if site is None:
        Site.objects.create(
            pk=getattr(settings, 'SITE_ID', 1),
            domain=netloc,
            name='NZDWorld',
        )
        return

    if (site.domain or '').strip().lower() in PLACEHOLDER_DOMAINS:
        site.domain = netloc
        if not (site.name or '').strip():
            site.name = 'NZDWorld'
        site.save(update_fields=['domain', 'name'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_comment_user_alter_comment_email_and_more'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(set_canonical_site_domain, migrations.RunPython.noop),
    ]
