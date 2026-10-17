"""
Delete analytics/monitoring rows older than the configured retention windows.

Run this from cron (cPanel -> Cron Jobs), e.g. nightly:
    cd /home/USER/project && /home/USER/virtualenv/.../bin/python manage.py prune_analytics

Nothing here touches content, users, comments or subscriptions -- only the
analytics tables.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from controlcenter.models import (
    AnalyticsIssue,
    PageVisit,
    RawRequestStat,
    SlowRequestLog,
    ThrottleCounter,
    ThrottleEvent,
)


class Command(BaseCommand):
    help = 'Prune analytics and operational monitoring tables.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--analytics-days', type=int, default=None)
        parser.add_argument('--raw-days', type=int, default=None)
        parser.add_argument('--operational-days', type=int, default=None)

    def handle(self, *args, **options):
        now = timezone.now()
        analytics_days = options['analytics_days'] or getattr(
            settings, 'ANALYTICS_RETENTION_DAYS', 180
        )
        raw_days = options['raw_days'] or getattr(
            settings, 'ANALYTICS_RAW_RETENTION_DAYS', 90
        )
        operational_days = options['operational_days'] or getattr(
            settings, 'ANALYTICS_OPERATIONAL_RETENTION_DAYS', 30
        )
        dry_run = options['dry_run']

        plan = [
            ('PageVisit', PageVisit.objects.filter(
                timestamp__lt=now - timedelta(days=analytics_days))),
            ('RawRequestStat', RawRequestStat.objects.filter(
                bucket_start__lt=now - timedelta(days=raw_days))),
            ('ThrottleEvent', ThrottleEvent.objects.filter(
                created_at__lt=now - timedelta(days=operational_days))),
            ('SlowRequestLog', SlowRequestLog.objects.filter(
                created_at__lt=now - timedelta(days=operational_days))),
            ('AnalyticsIssue', AnalyticsIssue.objects.filter(
                day__lt=(now - timedelta(days=operational_days)).date())),
            # Counters are only valid for their window; anything older is waste.
            ('ThrottleCounter', ThrottleCounter.objects.filter(
                window_start__lt=now - timedelta(days=2))),
        ]

        for label, queryset in plan:
            count = queryset.count()
            if dry_run:
                self.stdout.write(f'[dry-run] would delete {count} {label} rows')
                continue
            deleted = queryset.delete()[0] if count else 0
            self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} {label} rows'))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS('Analytics pruning complete.'))
