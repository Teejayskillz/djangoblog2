"""
Security, anti-abuse and analytics tests.

These use synthetic events created inside the test database only -- production
statistics are never touched.
"""

import datetime
import os
import tempfile
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

import core.testing  # noqa: F401  (Python 3.14 test-client patch)
from controlcenter import traffic
from controlcenter.models import (
    AnalyticsIssue,
    PageVisit,
    RawRequestStat,
    SlowRequestLog,
    ThrottleCounter,
    ThrottleEvent,
)
from controlcenter.throttle import check_rate_limit, reset
from core.antispam import make_form_token
from core.models import Comment, DownloadQuality
from core.testing import make_post, make_user

HUMAN_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36'
HTML_ACCEPT = 'text/html,application/xhtml+xml'


class ThrottleTestBase(TestCase):
    def setUp(self):
        cache.clear()  # LocMem block state is process-global

    def get_with_ip(self, path, ip='127.0.0.1', **extra):
        return self.client.get(path, REMOTE_ADDR=ip, HTTP_USER_AGENT=HUMAN_UA, **extra)

    def post_with_ip(self, path, data, ip='127.0.0.1'):
        return self.client.post(
            path, data, REMOTE_ADDR=ip, HTTP_USER_AGENT=HUMAN_UA,
            HTTP_ACCEPT=HTML_ACCEPT,
        )


@override_settings(RATE_LIMITS={'login': {'hits': 3, 'window': 300, 'cooldown': 300}})
class LoginThrottleTests(ThrottleTestBase):
    def test_login_is_rate_limited_per_ip(self):
        url = reverse('login')
        for _ in range(3):
            response = self.post_with_ip(url, {'username': 'nobody', 'password': 'wrong'})
            self.assertEqual(response.status_code, 200)

        blocked = self.post_with_ip(url, {'username': 'nobody', 'password': 'wrong'})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('Retry-After', blocked)
        self.assertContains(blocked, 'Too many requests', status_code=429)

    def test_limit_is_per_ip_not_global(self):
        url = reverse('login')
        for _ in range(3):
            self.post_with_ip(url, {'username': 'a', 'password': 'b'}, ip='10.0.0.1')

        self.assertEqual(
            self.post_with_ip(url, {'username': 'a', 'password': 'b'}, ip='10.0.0.1').status_code,
            429,
        )
        # A different visitor is unaffected.
        self.assertEqual(
            self.post_with_ip(url, {'username': 'a', 'password': 'b'}, ip='10.0.0.2').status_code,
            200,
        )

    def test_throttle_event_is_recorded_for_monitoring(self):
        url = reverse('login')
        for _ in range(4):
            self.post_with_ip(url, {'username': 'spam', 'password': 'x'})

        self.assertTrue(ThrottleEvent.objects.filter(scope='login').exists())

    def test_login_page_get_is_not_counted_against_the_limit(self):
        url = reverse('login')
        for _ in range(5):
            self.assertEqual(self.get_with_ip(url).status_code, 200)


@override_settings(RATE_LIMITS={
    'register': {'hits': 2, 'window': 3600, 'cooldown': 3600},
})
class RegisterThrottleTests(ThrottleTestBase):
    def test_registration_is_rate_limited(self):
        url = reverse('register')
        for index in range(2):
            response = self.post_with_ip(url, {
                'username': f'user{index}',
                'email': f'user{index}@example.com',
                'password1': 'Sup3rSecret!23',
                'password2': 'Sup3rSecret!23',
            })
            self.assertIn(response.status_code, (200, 302))

        blocked = self.post_with_ip(url, {
            'username': 'user99',
            'email': 'user99@example.com',
            'password1': 'Sup3rSecret!23',
            'password2': 'Sup3rSecret!23',
        })
        self.assertEqual(blocked.status_code, 429)


@override_settings(RATE_LIMITS={'search': {'hits': 2, 'window': 300, 'cooldown': 60}})
class SearchThrottleTests(ThrottleTestBase):
    def test_search_is_rate_limited(self):
        url = reverse('search')
        self.assertEqual(self.get_with_ip(f'{url}?q=movie').status_code, 200)
        self.assertEqual(self.get_with_ip(f'{url}?q=series').status_code, 200)

        blocked = self.get_with_ip(f'{url}?q=action')
        self.assertEqual(blocked.status_code, 429)

    def test_single_character_query_is_treated_as_empty(self):
        response = self.get_with_ip(f'{reverse("search")}?q=a')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['results'])


@override_settings(RATE_LIMITS={'download': {'hits': 1, 'window': 300, 'cooldown': 300}})
class DownloadThrottleTests(ThrottleTestBase):
    def setUp(self):
        super().setUp()
        self.post = make_post()
        self.quality = DownloadQuality.objects.create(
            post=self.post, quality='720p', download_url='https://cdn.example.com/film.mp4'
        )
        self.url = reverse('download_quality', args=[self.quality.pk])

    def test_download_links_are_rate_limited(self):
        first = self.get_with_ip(self.url)
        self.assertEqual(first.status_code, 302)

        blocked = self.get_with_ip(self.url)
        self.assertEqual(blocked.status_code, 429)
        self.assertTrue(ThrottleEvent.objects.filter(scope='download').exists())

    def test_download_counter_still_increments(self):
        self.get_with_ip(self.url)
        self.quality.refresh_from_db()
        self.assertEqual(self.quality.download_count, 1)


class ClientIpTests(ThrottleTestBase):
    @override_settings(TRUSTED_PROXY_IPS=())
    def test_forwarded_header_is_ignored_from_untrusted_peer(self):
        request = mock.Mock()
        request.META = {
            'REMOTE_ADDR': '203.0.113.9',
            'HTTP_X_FORWARDED_FOR': '1.2.3.4',
        }
        self.assertEqual(traffic.get_client_ip(request), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_IPS=('10.0.0.1',))
    def test_forwarded_header_is_used_from_trusted_proxy(self):
        request = mock.Mock()
        request.META = {
            'REMOTE_ADDR': '10.0.0.1',
            'HTTP_X_FORWARDED_FOR': '198.51.100.7, 10.0.0.1',
        }
        self.assertEqual(traffic.get_client_ip(request), '198.51.100.7')


@override_settings(
    FORM_MIN_SUBMIT_SECONDS=0,
    SPAM_MAX_LINKS=2,
    COMMENT_MIN_INTERVAL_SECONDS=0,
    COMMENT_MAX_PER_HOUR=1000,
    RATE_LIMITS={'comment': {'hits': 50, 'window': 3600, 'cooldown': 60}},
)
class CommentAbuseTests(ThrottleTestBase):
    def setUp(self):
        super().setUp()
        self.post = make_post(title='Comment Target', slug='comment-target')
        self.url = self.post.get_absolute_url()
        self.user = make_user('talker')
        self.client.force_login(self.user)

    def post_comment(self, text, token=None, **extra):
        data = {
            'comment': text,
            'form_token': token if token is not None else make_form_token(),
            'website': '',
        }
        data.update(extra)
        return self.client.post(
            self.url, data, REMOTE_ADDR='127.0.0.1',
            HTTP_USER_AGENT=HUMAN_UA, HTTP_ACCEPT=HTML_ACCEPT,
        )

    def test_valid_comment_is_accepted(self):
        response = self.post_comment('This is a perfectly normal comment.')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Comment.objects.filter(user=self.user).count(), 1)

    def test_logged_out_visitor_cannot_post_directly(self):
        self.client.logout()

        response = self.post_comment('Drive-by comment')

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])
        self.assertFalse(Comment.objects.exists())

    def test_submission_that_is_too_fast_is_rejected(self):
        with override_settings(FORM_MIN_SUBMIT_SECONDS=5):
            response = self.post_comment('Submitted instantly by a script')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'could not be submitted')
        self.assertFalse(Comment.objects.exists())
        self.assertTrue(AnalyticsIssue.objects.filter(kind='comment_rejected').exists())

    def test_missing_form_token_is_rejected(self):
        response = self.post_comment('No token at all', token='')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Comment.objects.exists())

    def test_link_spam_is_rejected(self):
        spam = 'Buy now https://a.example https://b.example https://c.example'

        response = self.post_comment(spam)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too many links')
        self.assertFalse(Comment.objects.exists())
        self.assertTrue(AnalyticsIssue.objects.filter(kind='comment_spam').exists())

    def test_honeypot_is_still_enforced(self):
        response = self.post_comment('Bot comment', website='http://spam.example')

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Comment.objects.exists())

    def test_html_is_escaped_in_stored_and_rendered_output(self):
        self.post_comment('<script>alert("x")</script> all good')
        Comment.objects.update(is_approved=True)

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_comment_rate_limit_blocks_direct_flooding(self):
        with override_settings(RATE_LIMITS={'comment': {'hits': 2, 'window': 3600, 'cooldown': 90}}):
            cache.clear()
            for index in range(2):
                self.assertEqual(
                    self.post_comment(f'Comment number {index} is unique enough').status_code,
                    302,
                )
            blocked = self.post_comment('One comment too many, still unique')

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(Comment.objects.filter(user=self.user).count(), 2)

    def test_premium_badge_still_comes_from_the_subscription(self):
        from core.testing import give_subscription

        give_subscription(self.user, days=30)
        self.post_comment('Premium member comment')
        Comment.objects.update(is_approved=True)

        self.assertIn('VIP subscriber', self.client.get(self.url).content.decode())


class BotClassificationTests(ThrottleTestBase):
    def test_claimed_googlebot_without_verification_is_not_trusted(self):
        with override_settings(BOT_VERIFY_CRAWLERS=True):
            with mock.patch('controlcenter.traffic._resolve_hostname', return_value=None):
                result = traffic.classify_request(self._request('66.249.66.1', 'Googlebot/2.1'))

        self.assertEqual(result['traffic_class'], 'known_bot')
        self.assertEqual(result['bot_name'], 'Googlebot')

    @override_settings(BOT_VERIFY_CRAWLERS=True)
    def test_verified_googlebot_requires_matching_forward_dns(self):
        with mock.patch(
            'controlcenter.traffic._resolve_hostname',
            return_value='crawl-66-249-66-1.googlebot.com',
        ), mock.patch(
            'controlcenter.traffic._forward_resolve',
            return_value={'66.249.66.1'},
        ):
            result = traffic.classify_request(self._request('66.249.66.1', 'Googlebot/2.1'))

        self.assertEqual(result['traffic_class'], 'verified_bot')

    @override_settings(BOT_VERIFY_CRAWLERS=True)
    def test_spoofed_reverse_dns_does_not_verify(self):
        with mock.patch(
            'controlcenter.traffic._resolve_hostname',
            return_value='crawl-66-249-66-1.googlebot.com',
        ), mock.patch(
            'controlcenter.traffic._forward_resolve',
            return_value={'203.0.113.5'},  # forward lookup does not match
        ):
            result = traffic.classify_request(self._request('66.249.66.1', 'Googlebot/2.1'))

        self.assertEqual(result['traffic_class'], 'known_bot')

    def test_unknown_user_agent_is_suspected_not_human(self):
        result = traffic.classify_request(self._request('198.51.100.3', ''))

        self.assertEqual(result['traffic_class'], 'suspected_bot')

    def test_normal_browser_is_estimated_human(self):
        result = traffic.classify_request(self._request('198.51.100.4', HUMAN_UA))

        self.assertEqual(result['traffic_class'], 'human')

    @staticmethod
    def _request(ip, user_agent):
        request = mock.Mock()
        request.META = {
            'REMOTE_ADDR': ip,
            'HTTP_USER_AGENT': user_agent,
            'HTTP_ACCEPT': HTML_ACCEPT,
        }
        return request


class PageViewRecordingTests(ThrottleTestBase):
    def test_page_views_are_recorded_for_html_pages(self):
        self.client.get('/', REMOTE_ADDR='198.51.100.10', HTTP_USER_AGENT=HUMAN_UA,
                        HTTP_ACCEPT=HTML_ACCEPT)

        self.assertTrue(PageVisit.objects.filter(path='/', traffic_class='human').exists())

    def test_static_assets_are_not_page_views(self):
        self.client.get('/static/css/site.css', REMOTE_ADDR='198.51.100.11',
                        HTTP_USER_AGENT=HUMAN_UA, HTTP_ACCEPT='text/css')

        self.assertFalse(PageVisit.objects.filter(path__startswith='/static/').exists())

    def test_control_center_and_admin_traffic_is_excluded(self):
        staff = make_user('ccstaff', is_staff=True, is_superuser=True)
        self.client.force_login(staff)

        self.client.get('/control-center/', REMOTE_ADDR='198.51.100.12',
                        HTTP_USER_AGENT=HUMAN_UA, HTTP_ACCEPT=HTML_ACCEPT)

        self.assertFalse(PageVisit.objects.exists())

    def test_duplicate_page_view_is_dropped_and_counted(self):
        for _ in range(2):
            self.client.get('/', REMOTE_ADDR='198.51.100.13', HTTP_USER_AGENT=HUMAN_UA,
                            HTTP_ACCEPT=HTML_ACCEPT)

        self.assertEqual(PageVisit.objects.filter(path='/').count(), 1)
        self.assertTrue(AnalyticsIssue.objects.filter(kind='duplicate').exists())

    def test_bot_page_view_is_classified_separately(self):
        self.client.get('/', REMOTE_ADDR='198.51.100.14',
                        HTTP_USER_AGENT='Mozilla/5.0 (compatible; AhrefsBot/7.0)',
                        HTTP_ACCEPT=HTML_ACCEPT)

        visit = PageVisit.objects.get(path='/')
        self.assertEqual(visit.traffic_class, 'known_bot')
        self.assertEqual(visit.device_type, 'bot')


@override_settings(RATE_LIMITS={})
class AnalyticsAggregationTests(ThrottleTestBase):
    def setUp(self):
        super().setUp()
        self.staff = make_user('analyst', is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)
        now = timezone.now()
        PageVisit.objects.create(
            path='/', visitor_id='human1', traffic_class='human', session_key='s1',
            timestamp=now, is_page_view=True,
        )
        PageVisit.objects.create(
            path='/movies/', visitor_id='human2', traffic_class='human', session_key='s2',
            timestamp=now, is_page_view=True,
        )
        PageVisit.objects.create(
            path='/', visitor_id='bot1', traffic_class='verified_bot', device_type='bot',
            timestamp=now, is_page_view=True,
        )
        PageVisit.objects.create(
            path='/', visitor_id='bot2', traffic_class='known_bot', device_type='bot',
            timestamp=now, is_page_view=True,
        )
        PageVisit.objects.create(
            path='/old/', visitor_id='human1', traffic_class='human',
            timestamp=now - datetime.timedelta(days=10), is_page_view=True,
        )

    def test_traffic_classes_are_reported_separately(self):
        response = self.client.get('/control-center/analytics/?range=today')
        context = response.context

        self.assertEqual(context['traffic_by_class']['human'], 2)
        self.assertEqual(context['traffic_by_class']['verified_bot'], 1)
        self.assertEqual(context['traffic_by_class']['known_bot'], 1)
        self.assertEqual(context['estimated_unique_visitors'], 2)
        self.assertEqual(context['estimated_sessions'], 2)

    def test_range_boundaries_use_utc_days(self):
        today = self.client.get('/control-center/analytics/?range=today').context
        week = self.client.get('/control-center/analytics/?range=7d').context

        self.assertEqual(today['human_views'], 2)
        # The 10-day-old row is outside every range.
        self.assertEqual(week['human_views'], 2)

    def test_raw_request_totals_come_only_from_the_import(self):
        RawRequestStat.objects.create(
            bucket_start=timezone.now().replace(minute=0, second=0, microsecond=0),
            path='/', method='GET', status_class='2xx', requests=42,
        )
        RawRequestStat.objects.create(
            bucket_start=timezone.now().replace(minute=0, second=0, microsecond=0),
            path='/wp-login.php', method='POST', status_class='4xx', requests=7,
        )

        context = self.client.get('/control-center/analytics/?range=today').context

        self.assertEqual(context['raw_requests'], 49)
        self.assertEqual(context['raw_by_status']['4xx'], 7)

    def test_slow_requests_are_reported(self):
        SlowRequestLog.objects.create(
            path='/slow/', view_name='core:home', duration_ms=2500, status_code=200
        )

        context = self.client.get('/control-center/analytics/?range=today').context

        self.assertEqual(context['slow_summary']['count'], 1)
        self.assertEqual(context['slow_summary']['max_ms'], 2500)

    def test_dashboard_shows_human_traffic_and_security_metrics(self):
        ThrottleEvent.objects.create(scope='login', path='/accounts/login/')
        response = self.client.get('/control-center/')

        self.assertEqual(response.context['stats']['today_views'], 2)
        self.assertEqual(response.context['stats']['today_bots'], 2)
        self.assertEqual(response.context['security']['throttled_24h'], 1)
        self.assertContains(response, 'Traffic &amp; security')

    def test_non_staff_cannot_read_analytics(self):
        self.client.logout()
        plain = make_user('curious')
        self.client.force_login(plain)

        response = self.client.get('/control-center/analytics/')

        self.assertIn(response.status_code, (302, 403))

    def test_analytics_page_renders_for_staff(self):
        response = self.client.get('/control-center/analytics/?range=7d')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Traffic quality')
        self.assertContains(response, 'Analytics health')


class ManagementCommandTests(ThrottleTestBase):
    def test_prune_analytics_dry_run_keeps_rows(self):
        PageVisit.objects.create(
            path='/', visitor_id='old', traffic_class='human',
            timestamp=timezone.now() - datetime.timedelta(days=400), is_page_view=True,
        )

        call_command('prune_analytics', '--dry-run', stdout=open(os.devnull, 'w'))

        self.assertTrue(PageVisit.objects.exists())

    def test_prune_analytics_deletes_only_old_rows(self):
        old = PageVisit.objects.create(
            path='/', visitor_id='old', traffic_class='human',
            timestamp=timezone.now() - datetime.timedelta(days=400), is_page_view=True,
        )
        fresh = PageVisit.objects.create(
            path='/', visitor_id='fresh', traffic_class='human',
            timestamp=timezone.now(), is_page_view=True,
        )

        call_command('prune_analytics', '--analytics-days', '180',
                     stdout=open(os.devnull, 'w'))

        self.assertFalse(PageVisit.objects.filter(pk=old.pk).exists())
        self.assertTrue(PageVisit.objects.filter(pk=fresh.pk).exists())

    def test_import_access_log_aggregates_requests(self):
        log_lines = (
            '1.1.1.1 - - [17/Oct/2026:03:23:11 +0000] "GET / HTTP/1.1" 200 1234 "-" "UA"\n'
            '1.1.1.2 - - [17/Oct/2026:03:23:45 +0000] "GET / HTTP/1.1" 200 1234 "-" "UA"\n'
            '1.1.1.3 - - [17/Oct/2026:03:24:02 +0000] "POST /wp-login.php HTTP/1.1" 404 0 "-" "UA"\n'
            'garbage line that should be skipped\n'
        )
        handle = tempfile.NamedTemporaryFile('w', suffix='.log', delete=False, encoding='utf-8')
        handle.write(log_lines)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        call_command('import_access_log', '--path', handle.name, stdout=open(os.devnull, 'w'))

        home = RawRequestStat.objects.get(path='/', method='GET', status_class='2xx')
        self.assertEqual(home.requests, 2)
        self.assertEqual(home.bucket_start.minute, 0)
        self.assertTrue(
            RawRequestStat.objects.filter(
                path='/wp-login.php', status_class='4xx', requests=1
            ).exists()
        )
