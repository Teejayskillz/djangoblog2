from django.test import TestCase, Client, override_settings
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import resolve, reverse
from django.utils import timezone
from django.template.context import BaseContext
from ads.models import Ad
from core.models import Category, Page, Post
from controlcenter.models import PageVisit
from controlcenter.slug_utils import build_unique_slug, get_slug_sources
from sports.models import SportsMatch
from subscriptions.models import SubscriptionPlan

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

        # Deep admin links keep their path under the Control Center prefix.
        resp_sub = self.client.get('/admin/core/post/')
        self.assertEqual(resp_sub.status_code, 302)
        self.assertEqual(resp_sub.url, '/control-center/core/post/')


class AdminRetirementTests(TestCase):
    """
    The built-in Django admin is retired from normal use but must stay
    installed, registered and reversible (302, never 301). Deep links are
    translated to their Control Center equivalent.
    """

    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_superuser(
            username='staffadmin', email='staffadmin@example.com', password='pass'
        )

    def test_deep_admin_links_keep_their_path(self):
        expected = {
            '/admin/core/post/': '/control-center/core/post/',
            '/admin/core/post/add/': '/control-center/core/post/add/',
            '/admin/core/category/3/change/': '/control-center/core/category/3/change/',
            '/admin/core/category/3/delete/': '/control-center/core/category/3/delete/',
            '/admin/core/post/25/view/': '/control-center/core/post/25/view/',
            '/admin/subscriptions/paymenttransaction/':
                '/control-center/subscriptions/paymenttransaction/',
            # The exact URL shape reported by the site owner:
            '/admin/subscriptions/paymenttransaction/25/change/':
                '/control-center/subscriptions/paymenttransaction/25/change/',
            '/admin/sports/sportsmatch/7/change/':
                '/control-center/sports/sportsmatch/7/change/',
            '/admin/ads/ad/4/change/': '/control-center/ads/ad/4/change/',
            # Control Center logout exists, so an old admin logout link maps too.
            '/admin/logout/': '/control-center/logout/',
        }
        for source, target in expected.items():
            with self.subTest(path=source):
                resp = self.client.get(source)
                self.assertEqual(resp.status_code, 302, f"{source} did not 302")
                self.assertEqual(resp['Location'], target)
                # The translated target must be a real Control Center route.
                self.assertIn('controlcenter', resolve(target).namespaces)

    def test_admin_root_and_unknown_admin_routes_fall_back_to_dashboard(self):
        # Admin-only URLs have no Control Center counterpart, so they must not
        # 302 into a dead Control Center path.
        for path in (
            '/admin',
            '/admin/',
            '/admin/login/',
            '/admin/password_change/',
            '/admin/jsi18n/',
            '/admin/autocomplete/',
            '/admin/core/',                      # app index
            '/admin/core/post/5/history/',       # admin-only history view
            '/admin/core/post/abc/change/',      # non-numeric pk
            '/admin/core/post/5/unknown-action/',
            '/admin/notanapp/notamodel/',        # unknown app/model pair
            '/admin/core/notamodel/',            # known app, unknown model
        ):
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 302, f"{path} did not 302")
                self.assertEqual(resp['Location'], '/control-center/')

    def test_admin_redirect_is_temporary_not_permanent(self):
        # No 301 anywhere in the chain: the move must stay reversible.
        for path in (
            '/admin',
            '/admin/',
            '/admin/core/post/',
            '/admin/subscriptions/paymenttransaction/25/change/',
        ):
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 301)

    def test_admin_without_trailing_slash_is_single_hop(self):
        # Handled by our own view, not by CommonMiddleware's 301 APPEND_SLASH.
        resp = self.client.get('/admin')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/')

        # A deep path without its trailing slash still maps to the right page.
        resp = self.client.get('/admin/core/post')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/core/post/')

    def test_admin_query_string_is_dropped_without_error(self):
        resp = self.client.get('/admin/core/post/?q=vip&page=2&o=1')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/core/post/')

    def test_admin_redirect_does_not_loop(self):
        resp = self.client.get('/admin/', follow=True)
        self.assertEqual(resp.redirect_chain[0], ('/control-center/', 302))
        for url, _status in resp.redirect_chain:
            self.assertFalse(
                url.startswith('/admin'),
                f"redirect loop detected back into the retired admin: {url}",
            )
        self.assertEqual(resp.status_code, 200)

    def test_deep_admin_link_reaches_the_control_center_login_then_the_page(self):
        # Anonymous: deep link -> matching Control Center page -> login page.
        resp = self.client.get('/admin/subscriptions/paymenttransaction/25/change/')
        self.assertEqual(resp['Location'], '/control-center/subscriptions/paymenttransaction/25/change/')
        login = self.client.get(resp['Location'])
        self.assertEqual(login.status_code, 302)
        self.assertIn('/accounts/login/', login['Location'])
        self.assertIn('next=/control-center/subscriptions/paymenttransaction/25/change/', login['Location'])

    def test_admin_redirect_answers_non_get_methods(self):
        # An old bookmarked admin form posting back must not 405/500.
        resp = self.client.post('/admin/login/', {'username': 'x', 'password': 'y'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], '/control-center/')

        resp = self.client.post(
            '/admin/subscriptions/paymenttransaction/25/change/',
            {'status': 'completed'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp['Location'],
            '/control-center/subscriptions/paymenttransaction/25/change/',
        )

    def test_admin_redirect_is_a_named_url(self):
        match = resolve('/admin/core/post/')
        self.assertEqual(match.url_name, 'admin_retired')
        self.assertEqual(match.kwargs, {'subpath': 'core/post/'})
        self.assertEqual(reverse('admin_retired'), '/admin')
        self.assertEqual(reverse('admin_retired', kwargs={'subpath': 'core/post/'}), '/admin/core/post/')

    def test_retired_admin_hits_are_not_tracked_as_public_traffic(self):
        self.client.get('/admin/')
        self.client.get('/admin/subscriptions/paymenttransaction/25/change/')
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


class SlugAutoGenerationInControlCenterTests(TestCase):
    """
    A blank slug box in the Control Center must auto-fill from the source field
    (title/name), exactly like prepopulated_fields does in the Django admin.
    """

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_superuser('slugstaff', 'slugstaff@example.com', 'pass')
        self.client.force_login(self.staff)

    @staticmethod
    def _inline_management(prefix, total=0):
        """Management form data a browser would send for an inline formset."""
        return {
            f'{prefix}-TOTAL_FORMS': str(total),
            f'{prefix}-INITIAL_FORMS': '0',
            f'{prefix}-MIN_NUM_FORMS': '0',
            f'{prefix}-MAX_NUM_FORMS': '1000',
        }

    # ------------------------------------------------------------------ #
    # Generic coverage: every slug field in every registered model
    # ------------------------------------------------------------------ #
    def test_every_registered_slug_model_has_an_auto_slug_source(self):
        from django.contrib import admin as django_admin
        from django.db import models as django_models

        checked = 0
        for model, modeladmin in django_admin.site._registry.items():
            for field in model._meta.fields:
                if not isinstance(field, django_models.SlugField):
                    continue
                checked += 1
                with self.subTest(model=model._meta.label, field=field.name):
                    self.assertIn(
                        field.name,
                        get_slug_sources(model, modeladmin),
                        f'{model._meta.label}.{field.name} has no auto-slug source field',
                    )
        # Post, Category, Page, Tag, Ad, SubscriptionPlan, SportsMatch
        self.assertGreaterEqual(checked, 7)

    def test_known_models_map_to_the_expected_source_fields(self):
        from django.contrib import admin as django_admin

        expected = {
            'core.Post': 'title',
            'core.Category': 'name',
            'core.Page': 'title',
            'sports.SportsMatch': 'title',
            'subscriptions.SubscriptionPlan': 'name',
            'ads.Ad': 'name',
        }
        for label, source in expected.items():
            model = next(m for m in django_admin.site._registry if m._meta.label == label)
            modeladmin = django_admin.site._registry[model]
            with self.subTest(model=label):
                self.assertEqual(get_slug_sources(model, modeladmin), {'slug': source})

    # ------------------------------------------------------------------ #
    # End to end: create through the Control Center with a blank slug
    # ------------------------------------------------------------------ #
    def test_category_slug_auto_generates_from_name(self):
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['core', 'category']),
            {'name': 'Action Movies', 'slug': ''},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Category.objects.get(name='Action Movies').slug, 'action-movies')

    def test_page_slug_auto_generates_from_title(self):
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['core', 'page']),
            {'title': 'About Us 2026', 'slug': '', 'content': '<p>Hello</p>'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Page.objects.get(title='About Us 2026').slug, 'about-us-2026')

    def test_post_slug_auto_generates_from_title(self):
        author = User.objects.create_user('writer', 'writer@example.com', 'pass')
        category = Category.objects.create(name='Series', slug='series')
        payload = {
            'title': 'Henry Danger S1 EP20',
            'slug': '',
            'content': '<p>episode</p>',
            'author': author.pk,
            'category': category.pk,
            'tags': 'comedy, nick',
        }
        payload.update(self._inline_management('downloadquality_set'))
        payload.update(self._inline_management('subtitle_set'))

        resp = self.client.post(reverse('controlcenter:model_add', args=['core', 'post']), payload)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Post.objects.get(title='Henry Danger S1 EP20').slug, 'henry-danger-s1-ep20')

    def test_sports_match_slug_auto_generates_from_title(self):
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['sports', 'sportsmatch']),
            {
                'title': 'Chelsea vs Arsenal',
                'slug': '',
                'sport': 'Football',
                'match_date': '2026-08-20',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SportsMatch.objects.get().slug, 'chelsea-vs-arsenal')

    def test_subscription_plan_slug_auto_generates_from_name(self):
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['subscriptions', 'subscriptionplan']),
            {
                'name': 'VIP Monthly',
                'slug': '',
                'price': '1000.00',
                'currency': 'NGN',
                'duration_days': '30',
                'display_order': '0',
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(SubscriptionPlan.objects.get().slug, 'vip-monthly')

    def test_ad_slug_is_filled_for_blank_optional_slug(self):
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['ads', 'ad']),
            {'name': 'Homepage Banner', 'slug': '', 'ad_content': '<div>ad</div>'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Ad.objects.get().slug, 'homepage-banner')

    # ------------------------------------------------------------------ #
    # Edge cases
    # ------------------------------------------------------------------ #
    def test_duplicate_source_values_get_unique_slugs(self):
        Category.objects.create(name='Series One', slug='series-one')

        # Different name, identical slug once slugified.
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['core', 'category']),
            {'name': 'Series  One', 'slug': ''},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Category.objects.get(name='Series  One').slug, 'series-one-2')

    def test_manually_entered_slug_is_never_overwritten(self):
        resp = self.client.post(
            reverse('controlcenter:model_add', args=['core', 'category']),
            {'name': 'Action Movies', 'slug': 'my-own-slug'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Category.objects.get(name='Action Movies').slug, 'my-own-slug')

    def test_edit_with_blank_slug_regenerates_and_excludes_itself(self):
        page = Page.objects.create(title='Old Title', slug='old-title', content='x')

        resp = self.client.post(
            reverse('controlcenter:model_change', args=['core', 'page', page.pk]),
            {'title': 'New Title', 'slug': '', 'content': 'x'},
        )
        self.assertEqual(resp.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.slug, 'new-title')

    def test_edit_keeping_same_title_keeps_same_slug(self):
        page = Page.objects.create(title='About Us', slug='about-us', content='x')

        resp = self.client.post(
            reverse('controlcenter:model_change', args=['core', 'page', page.pk]),
            {'title': 'About Us', 'slug': '', 'content': 'x'},
        )
        self.assertEqual(resp.status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.slug, 'about-us')

    def test_form_page_renders_the_slug_prefill_payload_and_script(self):
        resp = self.client.get(reverse('controlcenter:model_add', args=['core', 'post']))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="cc-slug-sources"', html)
        self.assertIn('"slug": "title"', html)
        self.assertIn('slugify', html)

    def test_form_page_for_model_without_slug_still_renders(self):
        resp = self.client.get(reverse('controlcenter:model_add', args=['core', 'comment']))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            '<script id="cc-slug-sources" type="application/json">{}</script>',
            resp.content.decode(),
        )

    def test_form_page_for_edit_also_renders_the_slug_payload(self):
        # NOTE: Post.author has default=2, so the author must be set explicitly
        # or the row fails the test database's foreign-key check on teardown.
        post = Post.objects.create(
            title='Something', slug='something', content='x', author=self.staff
        )
        resp = self.client.get(
            reverse('controlcenter:model_change', args=['core', 'post', post.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn('"slug": "title"', resp.content.decode())

    def test_generated_slug_respects_field_max_length(self):
        # Category.slug is max_length=100
        self.assertEqual(len(build_unique_slug(Category, 'slug', 'x' * 300)), 100)

    def test_slugify_strips_accents_and_punctuation(self):
        self.assertEqual(
            build_unique_slug(Category, 'slug', "Café & Crème: L'Été!"),
            'cafe-creme-lete',
        )

    def test_non_latin_title_still_produces_a_usable_slug(self):
        slug = build_unique_slug(Category, 'slug', 'नमस्ते दुनिया')
        self.assertTrue(slug)
        self.assertNotIn(' ', slug)
