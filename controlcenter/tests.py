from django.test import TestCase, Client, override_settings
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import resolve, reverse
from django.utils import timezone
from django.template.context import BaseContext
from controlcenter.models import PageVisit

# Compatibility patch for Python 3.14 test client template context copying
def _basecontext_copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.dicts = self.dicts[:]
    return duplicate
BaseContext.__copy__ = _basecontext_copy

User = get_user_model()


class ControlCenterTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Create regular user and superuser
        self.regular_user = User.objects.create_user(username='regular', email='reg@example.com', password='pass')
        self.admin_user = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass')

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertIn(resp.status_code, (302, 301))

    def test_dashboard_requires_staff(self):
        self.client.force_login(self.regular_user)
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertIn(resp.status_code, (302, 403))

    def test_dashboard_for_staff(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Control Center')
        self.assertContains(resp, 'Dashboard')
        self.assertContains(resp, 'Site Analytics')

    def test_analytics_view_for_staff(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:analytics'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Site Analytics')
        self.assertContains(resp, 'Total Page Views')
        self.assertContains(resp, 'Unique Visitors')

    def test_model_list_post(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:model_list', args=['core', 'post']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Posts')
        self.assertContains(resp, 'Bulk Actions')

    def test_model_add_page(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:model_add', args=['core', 'post']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Add New Post')

    def test_traffic_analytics_middleware_tracking(self):
        # Public visitor requests homepage
        anon_client = Client()
        resp = anon_client.get('/')
        self.assertEqual(resp.status_code, 200)

        # Confirm PageVisit was recorded
        visits = PageVisit.objects.filter(path='/')
        self.assertTrue(visits.exists())
        visit = visits.first()
        self.assertEqual(visit.path, '/')
        self.assertFalse(visit.is_staff_user)

    def test_traffic_analytics_middleware_ignores_controlcenter(self):
        self.client.force_login(self.admin_user)
        self.client.get(reverse('controlcenter:dashboard'))
        
        # Verify /control-center/ was NOT recorded in public traffic analytics
        self.assertFalse(PageVisit.objects.filter(path__startswith='/control-center/').exists())

    def test_admin_redirects_to_control_center(self):
        resp = self.client.get('/admin/')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, '/control-center/')

        resp_sub = self.client.get('/admin/core/post/')
        self.assertEqual(resp_sub.status_code, 302)
        self.assertEqual(resp_sub.url, '/control-center/')


class AdminRetirementTests(TestCase):
    """
    The built-in Django admin is retired from normal use but must stay
    installed, registered and reversible (302, never 301).
    """

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            username='staffadmin', email='staffadmin@example.com', password='pass'
        )

    def test_all_public_admin_url_shapes_redirect_with_302(self):
        for path in (
            '/admin',
            '/admin/',
            '/admin/login/',
            '/admin/logout/',
            '/admin/core/post/',
            '/admin/core/post/add/',
            '/admin/subscriptions/paymenttransaction/',
        ):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 302, f"{path} did not 302")
                self.assertEqual(resp['Location'], '/control-center/')

    def test_admin_redirect_is_temporary_not_permanent(self):
        # No 301 anywhere in the chain: the move must stay reversible.
        for path in ('/admin', '/admin/', '/admin/core/post/'):
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 301)

    def test_admin_without_trailing_slash_is_single_hop(self):
        # Handled by our own view, not by CommonMiddleware's 301 APPEND_SLASH.
        resp = self.client.get('/admin')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/')

    def test_admin_query_string_is_dropped_without_error(self):
        resp = self.client.get('/admin/core/post/?q=vip&page=2&o=1')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/')

    def test_admin_redirect_does_not_loop(self):
        resp = self.client.get('/admin/', follow=True)
        self.assertEqual(resp.redirect_chain[0], ('/control-center/', 302))
        for url, _status in resp.redirect_chain:
            self.assertFalse(
                url.startswith('/admin'),
                f"redirect loop detected back into the retired admin: {url}",
            )
        self.assertEqual(resp.status_code, 200)

    def test_admin_redirect_answers_non_get_methods(self):
        # An old bookmarked admin form posting back must not 405/500.
        resp = self.client.post('/admin/login/', {'username': 'x', 'password': 'y'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/')

    def test_admin_redirect_is_a_queryless_url_name(self):
        match = resolve('/admin/core/post/')
        self.assertEqual(match.url_name, 'admin_retired')
        self.assertEqual(reverse('admin_retired'), '/admin')

    def test_retired_admin_hits_are_not_tracked_as_public_traffic(self):
        self.client.get('/admin/')
        self.assertFalse(PageVisit.objects.filter(path__startswith='/admin').exists())

    def test_django_admin_app_still_installed_and_mounted(self):
        # App preserved
        self.assertIn('django.contrib.admin', settings.INSTALLED_APPS)

        # URL set still mounted and reversible (rollback friendly)
        admin_index = reverse('admin:index')
        self.assertEqual(admin_index, '/django-admin-backend-internal/')

        # Anonymous visitors hitting the private prefix get the admin login
        resp = self.client.get(admin_index)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/django-admin-backend-internal/login/', resp['Location'])

        # Staff can still reach the real admin directly if ever needed
        self.client.force_login(self.staff_user)
        resp = self.client.get(admin_index)
        self.assertEqual(resp.status_code, 200)


class ControlCenterAuthFlowTests(TestCase):
    """Unauthenticated users must land on a real login page, never in a loop."""

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            username='ccstaff', email='ccstaff@example.com', password='pass'
        )

    def test_anonymous_control_center_redirects_to_login_page(self):
        resp = self.client.get('/control-center/')
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp['Location'].startswith('/accounts/login/'))
        self.assertIn('next=/control-center/', resp['Location'])
        self.assertFalse(resp['Location'].startswith('/admin'))

    def test_login_page_renders_and_loop_terminates(self):
        resp = self.client.get('/control-center/')
        login_page = self.client.get(resp['Location'])
        self.assertEqual(login_page.status_code, 200)

    def test_non_staff_user_is_denied_control_center(self):
        regular = User.objects.create_user(username='plain', email='plain@example.com', password='pass')
        self.client.force_login(regular)
        resp = self.client.get('/control-center/')
        self.assertIn(resp.status_code, (302, 403))
        self.assertNotEqual(resp.status_code, 200)

    def test_staff_user_gets_control_center(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get('/control-center/')
        self.assertEqual(resp.status_code, 200)

    def test_logout_returns_to_login_with_next(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get('/control-center/logout/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/accounts/login/', resp['Location'])


class PublicRoutesUnaffectedTests(TestCase):
    """Retiring /admin/ must not shadow movies, downloads, blog or sports."""

    def test_public_paths_resolve_to_their_own_views(self):
        for path in (
            '/',
            '/sports/',
            '/sports-streaming/',
            '/download/quality/1/',
            '/download/subtitle/1/',
            '/media/',
            '/media/1/',
            '/category/movies/',
            '/tag/action/',
            '/pricing/',
            '/robots.txt',
            '/sitemap.xml',
            '/accounts/login/',
            '/control-center/',
            '/control-center/analytics/',
            '/control-center/core/post/',
        ):
            with self.subTest(path=path):
                self.assertNotEqual(resolve(path).url_name, 'admin_retired')

    def test_public_url_names_still_reverse(self):
        self.assertEqual(reverse('home'), '/')
        # NOTE: sports/urls.py is included twice ('sports/' and 'sports-streaming/'),
        # so the later include wins for reverse(); both paths still resolve.
        self.assertEqual(reverse('sports_list'), '/sports-streaming/')
        self.assertEqual(resolve('/sports/').url_name, 'sports_list')
        self.assertEqual(resolve('/sports-streaming/').url_name, 'sports_list')
        self.assertEqual(reverse('download_quality', args=[7]), '/download/quality/7/')
        self.assertEqual(reverse('media_list'), '/media/')
        self.assertEqual(reverse('controlcenter:dashboard'), '/control-center/')

    def test_pending_payment_link_target_exists(self):
        # Template link rewritten to the Control Center must resolve.
        self.assertEqual(
            resolve('/control-center/subscriptions/paymenttransaction/').url_name,
            'model_list',
        )
        self.assertEqual(
            reverse('controlcenter:model_change', args=['subscriptions', 'paymenttransaction', 5]),
            '/control-center/subscriptions/paymenttransaction/5/change/',
        )


class NoStaffLinkPointsAtRetiredAdminTests(TestCase):
    """
    Every in-app staff shortcut must now point at the Control Center, so normal
    use never bounces through the retired /admin/ URL.
    """

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            username='linkstaff', email='linkstaff@example.com', password='pass'
        )

    def test_public_header_staff_link_targets_control_center(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('href="/control-center/"', html)
        self.assertNotIn('href="/admin/"', html)

    def test_header_pending_payments_badge_targets_control_center(self):
        from subscriptions.models import PaymentTransaction

        tx_user = User.objects.create_user(username='txuser', email='tx@example.com', password='pass')
        PaymentTransaction.objects.create(
            user=tx_user, reference='TEST-REF-1', amount='1000.00', currency='NGN', status='pending'
        )
        self.client.force_login(self.staff_user)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(
            '/control-center/subscriptions/paymenttransaction/?status__exact=pending', html
        )
        self.assertNotIn('/admin/subscriptions/paymenttransaction/', html)

    def test_pricing_page_add_plan_link_targets_control_center(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get('/pricing/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('/control-center/subscriptions/subscriptionplan/add/', html)
        self.assertNotIn('/admin/', html)

    def test_subscribers_import_page_links_back_to_control_center(self):
        self.client.force_login(self.staff_user)
        resp = self.client.get('/import-sub/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('/control-center/', html)
        self.assertNotIn('/admin/', html)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_payment_alert_email_deep_links_into_control_center(self):
        from decimal import Decimal

        from django.core import mail
        from django.test import RequestFactory
        from subscriptions.emails import send_receipt_uploaded_notifications
        from subscriptions.models import PaymentTransaction

        tx_user = User.objects.create_user(username='payer', email='payer@example.com', password='pass')
        tx = PaymentTransaction.objects.create(
            user=tx_user, reference='TEST-REF-2', amount=Decimal('2500.00'), currency='NGN', status='pending'
        )
        User.objects.create_superuser(username='alertadmin', email='alertadmin@example.com', password='pass')

        # subscriptions/views.py always calls this with request=request
        request = RequestFactory().post('/subscriptions/checkout/')
        send_receipt_uploaded_notifications(tx, request=request)

        self.assertTrue(mail.outbox, "expected the receipt notification emails to be sent")
        content = ""
        for message in mail.outbox:
            content += message.subject + "\n" + message.body + "\n"
            for alt_body, _mimetype in message.alternatives:
                content += alt_body + "\n"

        expected = reverse(
            'controlcenter:model_change', args=['subscriptions', 'paymenttransaction', tx.id]
        )
        self.assertIn(expected, content)
        self.assertNotIn('/admin/subscriptions/paymenttransaction/', content)
