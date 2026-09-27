from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
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

        # Subscribed user gets redirected to the actual download link with VIP token attached!
        resp2 = self.client.get(dl_url)
        self.assertEqual(resp2.status_code, 302)
        quality.refresh_from_db()
        self.assertTrue(resp2.url.startswith(quality.download_url))
        self.assertIn('token=', resp2.url)

    def test_vip_token_generation(self):
        from subscriptions.utils import generate_cdn_vip_token, append_vip_token_to_url
        from django.core.signing import TimestampSigner, settings

        token = generate_cdn_vip_token(self.user.id)
        self.assertIsNotNone(token)
        
        # Verify unsigning using TimestampSigner and CDN_SHARED_SECRET
        signer = TimestampSigner(key=settings.CDN_SHARED_SECRET)
        unsigned_id = signer.unsign(token, max_age=300)
        self.assertEqual(unsigned_id, str(self.user.id))

        tokenized_url = append_vip_token_to_url("https://cdn.example.com/download/123", token)
        self.assertIn("token=", tokenized_url)
        from urllib.parse import unquote
        self.assertEqual(unquote(tokenized_url), f"https://cdn.example.com/download/123/?token={token}")

    def test_shortener_rejects_unexpected_domain(self):
        from core import utils
        from django.test.utils import override_settings

        with override_settings():
            original_allowed = utils.SHORTENER_ALLOWED_HOSTS
            original_legacy = utils.SHORTENER_LEGACY_HOSTS
            utils.SHORTENER_ALLOWED_HOSTS = ('cdn.nzdowlrd.com',)
            utils.SHORTENER_LEGACY_HOSTS = ()
            try:
                with patch('core.utils.requests.post') as mock_post:
                    mock_post.return_value.status_code = 200
                    mock_post.return_value.json.return_value = {
                        'short_url': 'https://dl.jaraflix.com/abc123/'
                    }

                    result = utils.shorten_url('https://example.com/download/movie.mp4', 'Movie Title')
                    self.assertEqual(result, 'https://example.com/download/movie.mp4')
            finally:
                utils.SHORTENER_ALLOWED_HOSTS = original_allowed
                utils.SHORTENER_LEGACY_HOSTS = original_legacy

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

    def test_email_notifications_on_receipt_upload_and_activation(self):
        from django.core import mail
        from django.core.files.uploadedfile import SimpleUploadedFile

        # Create two admin accounts with email addresses
        admin1 = User.objects.create_superuser('admin1', 'admin1@example.com', 'AdminPass123!')
        admin2 = User.objects.create_superuser('admin2', 'admin2@example.com', 'AdminPass123!')

        # Clear mail outbox
        mail.outbox = []

        self.client.login(username='testsubscriber', password='Password123!')
        dummy_receipt = SimpleUploadedFile(
            "receipt.jpg",
            b"fake_receipt_data",
            content_type="image/jpeg"
        )
        url = reverse('subscriptions:checkout', kwargs={'plan_slug': self.plan.slug})
        response = self.client.post(url, {
            'proof_of_payment': dummy_receipt,
            'user_notes': 'Payment for VIP access'
        })
        self.assertEqual(response.status_code, 302)

        # Verify two emails sent: 1 to user, 1 to all admins
        self.assertEqual(len(mail.outbox), 2)

        user_email = mail.outbox[0]
        self.assertEqual(user_email.to, ['subscriber@example.com'])
        self.assertIn("Payment Receipt Received", user_email.subject)
        self.assertIn("VIP Monthly", user_email.body)

        admin_email = mail.outbox[1]
        self.assertIn('admin1@example.com', admin_email.to)
        self.assertIn('admin2@example.com', admin_email.to)
        self.assertIn("[Admin Alert]", admin_email.subject)
        self.assertIn("testsubscriber", admin_email.body)

        # Now approve/complete the transaction
        mail.outbox = []
        tx = PaymentTransaction.objects.get(user=self.user)
        tx.complete_transaction()

        # Verify activation email sent to user
        self.assertEqual(len(mail.outbox), 1)
        activation_email = mail.outbox[0]
        self.assertEqual(activation_email.to, ['subscriber@example.com'])
        self.assertIn("VIP Subscription is Now Active", activation_email.subject)
        self.assertIn("VIP Monthly", activation_email.body)


