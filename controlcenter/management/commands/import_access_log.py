"""
Import aggregated raw HTTP request counts from the web server access log.

cPanel/CloudLinux (Apache + Passenger) writes a combined-format access log. The
web server sees every request -- including ones that never reach Django (blocked
by Apache, PHP probes, aggressive scans) -- so this is the only honest source for
"raw requests" and server-level 4xx/5xx volume.

Usage:
    python manage.py import_access_log                 # uses ACCESS_LOG_PATH
    python manage.py import_access_log --path /home/user/logs/access.log
    python manage.py import_access_log --max-lines 500000 --dry-run
"""

import gzip
import os
import re
from collections import Counter
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.utils import timezone

from controlcenter.models import RawRequestStat

# 1.2.3.4 - - [17/Oct/2026:03:23:11 +0000] "GET /path/ HTTP/1.1" 200 1234 "-" "UA"
LOG_RE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) (?P<size>\S+)'
)

MONTHS = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}


def status_class(status):
    if 200 <= status < 300:
        return '2xx'
    if 300 <= status < 400:
        return '3xx'
    if 400 <= status < 500:
        return '4xx'
    if 500 <= status < 600:
        return '5xx'
    return 'other'


class Command(BaseCommand):
    help = 'Aggregate raw HTTP requests from the web server access log.'

    def add_arguments(self, parser):
        parser.add_argument('--path', default=getattr(settings, 'ACCESS_LOG_PATH', ''))
        parser.add_argument('--max-lines', type=int, default=500000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        path = options['path']
        if not path:
            raise CommandError(
                'No access log path. Set the ACCESS_LOG_PATH environment variable '
                'or pass --path.'
            )
        if not os.path.exists(path):
            raise CommandError(f'Access log not found: {path}')

        opener = gzip.open if path.endswith('.gz') else open
        counts = Counter()
        matched = 0
        skipped = 0
        unparsed = 0

        with opener(path, 'rt', encoding='utf-8', errors='replace') as handle:
            for index, line in enumerate(handle):
                if index >= options['max_lines']:
                    break
                match = LOG_RE.match(line)
                if not match:
                    unparsed += 1
                    continue
                try:
                    stamp = datetime.strptime(
                        match.group('ts').split(' ')[0], '%d/%b/%Y:%H:%M:%S'
                    ).replace(tzinfo=dt_timezone.utc)
                except ValueError:
                    skipped += 1
                    continue

                bucket = stamp.replace(minute=0, second=0, microsecond=0)
                path_value = match.group('path').split('?')[0][:255] or '/'
                counts[(
                    bucket,
                    path_value,
                    match.group('method')[:10],
                    status_class(int(match.group('status'))),
                )] += 1
                matched += 1

        if options['dry_run']:
            self.stdout.write(
                f'[dry-run] parsed {matched} lines into {len(counts)} buckets '
                f'({skipped} bad dates, {unparsed} unparsed)'
            )
            return

        created = 0
        updated = 0
        for (bucket, path_value, method, klass), requests in counts.items():
            row, was_created = RawRequestStat.objects.get_or_create(
                bucket_start=bucket,
                path=path_value,
                method=method,
                status_class=klass,
                defaults={'requests': requests},
            )
            if was_created:
                created += 1
            else:
                RawRequestStat.objects.filter(pk=row.pk).update(
                    requests=F('requests') + requests,
                    updated_at=timezone.now(),
                )
                updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Imported {matched} log lines: {created} new buckets, {updated} updated, '
            f'{skipped} bad dates, {unparsed} unparsed lines.'
        ))
