from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from subscriptions.models import SubscriptionPlan, UserSubscription, PaymentTransaction
from subscriptions.utils import user_has_active_subscription
from ads.context_processors import ads_context
from ads.models import Ad
from core.models import Post, Category, DownloadQuality
from django.template.context import BaseContext

# Compatibility patch for Python 3.14 test client template context copying
def _basecontext_copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.dicts = self.dicts[:]
    return duplicate
BaseContext.__copy__ = _basecontext_copy


class SubscriptionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testsubscriber',
            email='subscriber@example.com',
            password='Password123!'
        )
        self.plan = SubscriptionPlan.objects.create(
            name="VIP Monthly",
            slug="vip-monthly",
            price=1500.00,
            currency="NGN",
            duration_days=30,
            is_active=True,
            features="100% Ad-Free Browsing\nVIP Downloads"
        )
        # Create an active ad
        self.ad = Ad.objects.create(
            name="Sidebar Banner",
            slug="sidebar1",
            ad_content="<div>Fake Ad Banner</div>",
            is_active=True
        )

    def test_plan_list_view(self):
        response = self.client.get(reverse('subscriptions:plan_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VIP Monthly")
        self.assertContains(response, "1,500")

    def test_pricing_shortcut_url(self):
        response = self.client.get(reverse('pricing'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "VIP Monthly")

    def test_checkout_login_required(self):
        # Unauthenticated user should be redirected to login
        response = self.client.get(reverse('subscriptions:checkout', kwargs={'plan_slug': self.plan.slug}))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_checkout_page_for_logged_in_user(self):
        self.client.login(username='testsubscriber', password='Password123!')
        response = self.client.get(reverse('subscriptions:checkout', kwargs={'plan_slug': self.plan.slug}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manual Bank Transfer Checkout")
        self.assertContains(response, "VIP Monthly")

    def test_manual_bank_transfer_payment(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.login(username='testsubscriber', password='Password123!')
        
        dummy_receipt = SimpleUploadedFile(
            "receipt.jpg",
            b"file_content_fake_image",
            content_type="image/jpeg"
        )
        url = reverse('subscriptions:checkout', kwargs={'plan_slug': self.plan.slug})
        response = self.client.post(url, {
            'proof_of_payment': dummy_receipt,
            'user_notes': 'Transferred from John Doe / GTBank'
        })
        self.assertEqual(response.status_code, 302)

        # Verify transaction created with status 'pending'
        tx = PaymentTransaction.objects.get(user=self.user)
        self.assertEqual(tx.status, 'pending')
        self.assertEqual(tx.payment_method, 'bank_transfer')
        self.assertEqual(tx.user_notes, 'Transferred from John Doe / GTBank')
        self.assertTrue(tx.proof_of_payment)

        # Admin completes transaction
        tx.complete_transaction()
        self.assertEqual(tx.status, 'completed')

        # Verify user subscription is active
        self.assertTrue(user_has_active_subscription(self.user))
        sub = UserSubscription.objects.get(user=self.user)
        self.assertTrue(sub.is_valid())
        self.assertEqual(sub.plan, self.plan)
        self.assertGreater(sub.days_remaining, 0)


    def test_ad_suppression_for_subscribers(self):
        # Without subscription, ads_context should return active ads
        class MockRequest:
            def __init__(self, user):
                self.user = user

        req_free = MockRequest(self.user)
        ctx_free = ads_context(req_free)
        self.assertIn('sidebar1', ctx_free['ads_by_slug'])

        # Now activate subscription
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True
        )

        req_sub = MockRequest(self.user)
        ctx_sub = ads_context(req_sub)
        self.assertEqual(ctx_sub['ads_by_slug'], {})

    def test_premium_download_protection(self):
        # Create category, post, and premium quality
        category = Category.objects.create(name="Action", slug="action")
        post = Post.objects.create(
            title="Iron Man 4",
            slug="iron-man-4",
            category=category,
            author=self.user,
            content="Great movie"
        )
        quality = DownloadQuality.objects.create(
            post=post,
            quality="1080p",
            download_url="https://example.com/download/iron-man-4.mp4",
            is_premium=True
        )

        # Unsubscribed user gets redirected to plans
        self.client.login(username='testsubscriber', password='Password123!')
        dl_url = reverse('download_quality', kwargs={'pk': quality.pk})
        resp = self.client.get(dl_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('subscriptions:plan_list'))

        # Now give user an active subscription
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=30),
            is_active=True
        )

        # Subscribed user gets redirected to the actual download link!
        resp2 = self.client.get(dl_url)
        self.assertEqual(resp2.status_code, 302)
        quality.refresh_from_db()
        self.assertEqual(resp2.url, quality.download_url)

    def test_subscription_extension(self):
        # Existing subscription with 10 days remaining
        initial_end = timezone.now() + timedelta(days=10)
        sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            start_date=timezone.now() - timedelta(days=20),
            end_date=initial_end,
            is_active=True
        )

        # Extending should add 30 days onto current end_date, not from now
        sub.extend_subscription(self.plan)
        expected_min_end = initial_end + timedelta(days=29)
        self.assertGreater(sub.end_date, expected_min_end)

    def test_transaction_status_change_auto_upgrades_vip(self):
        # Create a pending transaction
        tx = PaymentTransaction.objects.create(
            user=self.user,
            plan=self.plan,
            reference=PaymentTransaction.generate_reference(),
            amount=self.plan.price,
            currency=self.plan.currency,
            status='pending'
        )
        self.assertFalse(user_has_active_subscription(self.user))

        # Admin updates status directly to completed and saves
        tx.status = 'completed'
        tx.save()

        # Check that user subscription is now active with start_date and end_date set
        self.assertTrue(user_has_active_subscription(self.user))
        sub = UserSubscription.objects.get(user=self.user)
        self.assertTrue(sub.is_active)
        self.assertIsNotNone(sub.start_date)
        self.assertIsNotNone(sub.end_date)
        self.assertEqual(sub.plan, self.plan)

