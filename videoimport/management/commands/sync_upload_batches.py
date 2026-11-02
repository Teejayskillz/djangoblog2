"""
Refresh upload batches from the storage service (blog-side cron).

    python manage.py sync_upload_batches

Run every minute alongside the uploader's own queue worker. It only reads status
from the existing API and publishes a post when every queued row is confirmed.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from videoimport import publishing


class Command(BaseCommand):
    help = "Poll the uploader for queued batches and publish posts when all rows succeed."

    def handle(self, *args, **options):
        result = publishing.sync_all()
        self.stdout.write(
            f"checked {result['checked']} batch(es): "
            f"{result['completed']} completed, {result['failed']} failed"
        )
