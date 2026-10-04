from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from sports.models import SportsMatch
from subscriptions.models import SubscriptionPlan, UserSubscription
from django.template.context import BaseContext

# Compatibility patch for Python 3.14 test client template context copying
def _basecontext_copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.dicts = self.dicts[:]
    return duplicate
BaseContext.__copy__ = _basecontext_copy


class SportsStreamingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.regular_user = User.objects.create_user(
            username='regularuser',
            email='regular@example.com',
            password='Password123!'
        )
        self.vip_user = User.objects.create_user(
            username='vipuser',
            email='vip@example.com',
            password='Password123!'
        )

        # Give vip_user an active VIP subscription
        self.plan = SubscriptionPlan.objects.create(
            name="VIP Monthly",
            slug="vip-monthly",
            price=1500.00,
            currency="NGN",
            duration_days=30,
            is_active=True
        )
        UserSubscription.objects.create(
            user=self.vip_user,
            plan=self.plan,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True
        )

        # Create published sports match with embed code
        self.match = SportsMatch.objects.create(
            title="Arsenal vs Chelsea Live",
            slug="arsenal-vs-chelsea-live",
            sport="Football",
            team_home="Arsenal",
            team_away="Chelsea",
            description="London derby live stream coverage.",
            embed_code='<iframe src="https://stream.example.com/embed/123"></iframe>',
            is_published=True,
            is_featured=True
        )

        # Create unpublished sports match
        self.unpublished_match = SportsMatch.objects.create(
            title="Unpublished Secret Match",
            slug="unpublished-secret-match",
            sport="Boxing",
            embed_code='<iframe src="https://stream.example.com/secret"></iframe>',
            is_published=False
        )

    def test_sports_listing_page(self):
        response = self.client.get(reverse('sports_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Arsenal vs Chelsea Live")
        self.assertNotContains(response, "Unpublished Secret Match")
        self.assertContains(response, "Football")

    def test_sports_streaming_url_alias(self):
        # Path /sports-streaming/
        response = self.client.get('/sports-streaming/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Arsenal vs Chelsea Live")

    def test_homepage_integration(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sports Streaming")
        self.assertContains(response, "Arsenal vs Chelsea Live")
        self.assertNotContains(response, "Unpublished Secret Match")

    def test_anonymous_user_cannot_see_embed_code(self):
        # Logged-out user views match detail page
        url = reverse('sports_detail', kwargs={'slug': self.match.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Must show locked CTA
        self.assertContains(response, "VIP ACCESS REQUIRED")
        self.assertContains(response, "Subscribe for VIP Access")

        # MUST NOT contain embed code in rendered HTML response
        self.assertNotContains(response, "https://stream.example.com/embed/123")
        self.assertIsNone(response.context['embed_code'])
        self.assertFalse(response.context['is_vip'])

    def test_non_vip_logged_in_user_cannot_see_embed_code(self):
        self.client.login(username='regularuser', password='Password123!')
        url = reverse('sports_detail', kwargs={'slug': self.match.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Must show locked CTA
        self.assertContains(response, "VIP ACCESS REQUIRED")

        # MUST NOT contain embed code in rendered HTML response
        self.assertNotContains(response, "https://stream.example.com/embed/123")
        self.assertIsNone(response.context['embed_code'])
        self.assertFalse(response.context['is_vip'])

    def test_vip_user_can_see_embed_code(self):
        self.client.login(username='vipuser', password='Password123!')
        url = reverse('sports_detail', kwargs={'slug': self.match.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Must contain VIP Unlocked badge and iframe embed code
        self.assertContains(response, "VIP Stream Active")
        self.assertContains(response, "https://stream.example.com/embed/123")
        self.assertIsNotNone(response.context['embed_code'])
        self.assertTrue(response.context['is_vip'])

    def test_unpublished_match_returns_404(self):
        url = reverse('sports_detail', kwargs={'slug': self.unpublished_match.slug})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
