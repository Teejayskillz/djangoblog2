"""
Automated tests for the optional External Link Generator integration.

Run with::

    python manage.py test core.test_external_links -v 2

Covers the handoff, the signed server-to-server API, session expiry, replay,
single-use redemption, deleted/disabled quality rows, premium handling, rate
limiting, admin actions and the "nothing leaks before redemption" rule.
"""

import json
import re
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import external_link_service as service
from core.external_link_models import (
    ExternalLinkConfig,
    ExternalLinkEvent,
    ExternalLinkSession,
)
from core.models import Category, DownloadQuality, Post

User = get_user_model()

BLOG_URL = "https://blog.example.test"
ENTRY_TEMPLATE = "https://blog.example.test/nzdworld-link/{token}/"
# Kept on a shortener-allowed host so the existing pre_save shortener signal is
# skipped (no outbound HTTP inside unit tests).
DESTINATION = "https://cdn.nzdworld.com/files/movie-one-720p.mp4"
TOKEN_FROM_LOCATION = re.compile(r"/nzdworld-link/([A-Za-z0-9_-]+)/$")


class ExternalLinkTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user("owner", password="x")
        self.category = Category.objects.create(name="Movies", slug="movies")
        self.post = Post.objects.create(
            title="Movie One",
            slug="movie-one",
            content="<p>Body</p>",
            author=self.owner,
            category=self.category,
            is_published=True,
            enable_downloads=True,
        )
        self.quality = DownloadQuality.objects.create(
            post=self.post,
            quality="720p",
            download_url=DESTINATION,
        )
        self.config = ExternalLinkConfig.load()
        self.config.enabled = True
        self.config.wordpress_site_url = BLOG_URL
        self.config.wordpress_entry_template = ENTRY_TEMPLATE
        self.config.save()
        self.key = service.issue_api_key(self.config)
        self.config.refresh_from_db()

    # -- helpers -----------------------------------------------------------
    def handoff(self, quality=None, **params):
        return self.client.get(
            reverse("download_quality", args=[(quality or self.quality).pk]), **params
        )

    def token_from(self, response):
        match = TOKEN_FROM_LOCATION.search(response["Location"])
        self.assertIsNotNone(match, f"unexpected handoff URL: {response['Location']}")
        return match.group(1)

    def api(self, name, payload, *, key=None, timestamp=None, nonce=None,
            signature=None, secure=True, raw_body=None):
        key = self.key if key is None else key
        body = raw_body if raw_body is not None else json.dumps(payload)
        ts = str(int(timezone.now().timestamp())) if timestamp is None else str(timestamp)
        nce = nonce or secrets.token_hex(16)
        sig = signature or service.expected_signature(key, ts, nce, body.encode("utf-8"))
        return self.client.post(
            reverse(f"external_links:{name}"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {key}",
            HTTP_X_NZD_TIMESTAMP=ts,
            HTTP_X_NZD_NONCE=nce,
            HTTP_X_NZD_SIGNATURE=sig,
            secure=secure,
        )

    def validate(self, token, countdown=30, **kwargs):
        return self.api(
            "session_validate",
            {
                "site_url": BLOG_URL,
                "token": token,
                "countdown_seconds": countdown,
                "plugin_version": "1.0.0",
            },
            **kwargs,
        )

    def redeem(self, token, **kwargs):
        return self.api("session_redeem", {"site_url": BLOG_URL, "token": token}, **kwargs)

    def force_countdown_finished(self, token):
        session = ExternalLinkSession.objects.get(token_hash=service.hash_session_token(token))
        ExternalLinkSession.objects.filter(pk=session.pk).update(
            not_before=timezone.now() - timedelta(seconds=1)
        )
        return session


class DisabledIntegrationTests(ExternalLinkTestBase):
    def test_disabled_integration_keeps_the_original_download(self):
        ExternalLinkConfig.objects.filter(pk=self.config.pk).update(enabled=False)
        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], DESTINATION)
        self.assertFalse(ExternalLinkSession.objects.exists())
        self.quality.refresh_from_db()
        self.assertEqual(self.quality.download_count, 1)

    def test_revoked_key_restores_the_original_download(self):
        service.revoke_api_key(self.config)
        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], DESTINATION)
        self.assertFalse(ExternalLinkSession.objects.exists())

    def test_unconfigured_integration_falls_back_and_records_the_reason(self):
        ExternalLinkConfig.objects.filter(pk=self.config.pk).update(wordpress_site_url="")
        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], DESTINATION)
        self.config.refresh_from_db()
        self.assertIn("No WordPress site", self.config.last_handoff_error)

    def test_disabled_api_rejects_sessions_but_allows_a_connection_test(self):
        ExternalLinkConfig.objects.filter(pk=self.config.pk).update(enabled=False)
        validate = self.validate("a" * 43)
        self.assertEqual(validate.status_code, 403)
        self.assertEqual(validate.json()["error"]["code"], "integration_disabled")

        test = self.api(
            "connection_test",
            {
                "site_url": BLOG_URL,
                "entry_url_template": ENTRY_TEMPLATE,
                "plugin_version": "1.0.0",
            },
        )
        self.assertEqual(test.status_code, 200)
        self.assertFalse(test.json()["integration_enabled"])


class HandoffTests(ExternalLinkTestBase):
    def test_enabled_handoff_redirects_to_wordpress_with_an_opaque_token(self):
        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(BLOG_URL))
        self.assertNotIn("cdn.nzdworld.com", response["Location"])
        self.assertNotIn("/download/quality/", response["Location"])
        self.assertNotIn(str(self.quality.pk), response["Location"].split("/nzdworld-link/")[0])

        token = self.token_from(response)
        self.assertRegex(token, service.SESSION_TOKEN_RE)
        # the plaintext token must never be stored
        self.assertFalse(ExternalLinkSession.objects.filter(token_hash=token).exists())
        session = ExternalLinkSession.objects.get()
        self.assertEqual(session.token_hash, service.hash_session_token(token))
        self.assertEqual(session.quality_id, self.quality.pk)
        self.assertEqual(session.post_id, self.post.pk)
        self.assertEqual(session.site_url, BLOG_URL)

    def test_handoff_sets_no_store_and_no_referrer_headers(self):
        response = self.handoff()
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.content, b"")

    def test_repeated_clicks_create_independent_single_use_sessions(self):
        first = self.token_from(self.handoff())
        second = self.token_from(self.handoff())
        self.assertNotEqual(first, second)
        self.assertEqual(ExternalLinkSession.objects.count(), 2)

        for token in (first, second):
            self.assertEqual(self.validate(token).status_code, 200)
            self.force_countdown_finished(token)
            redeemed = self.redeem(token)
            self.assertEqual(redeemed.status_code, 200)
            self.assertEqual(redeemed.json()["destination_url"], DESTINATION)
        self.quality.refresh_from_db()
        self.assertEqual(self.quality.download_count, 2)

    def test_premium_without_vip_keeps_the_existing_gate(self):
        DownloadQuality.objects.filter(pk=self.quality.pk).update(is_premium=True)
        self.quality.refresh_from_db()
        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("subscriptions:plan_list"))
        self.assertFalse(ExternalLinkSession.objects.exists())

    def test_vip_session_reuses_the_cdn_token_helper(self):
        vip = User.objects.create_superuser("vip", "vip@example.test", "x")
        DownloadQuality.objects.filter(pk=self.quality.pk).update(is_premium=True)
        self.quality.refresh_from_db()
        self.client.force_login(vip)

        response = self.handoff()
        self.assertEqual(response.status_code, 302)
        session = ExternalLinkSession.objects.get()
        self.assertEqual(session.vip_user_id, vip.pk)

        token = self.token_from(response)
        self.validate(token, countdown=5)
        self.force_countdown_finished(token)
        redeemed = self.redeem(token)
        self.assertEqual(redeemed.status_code, 200)
        destination = redeemed.json()["destination_url"]
        self.assertTrue(destination.startswith("https://cdn.nzdworld.com/files/movie-one-720p.mp4/"))
        self.assertIn("token=", destination)

    @override_settings(RATE_LIMITS={"external_handoff": {"hits": 1, "window": 300, "cooldown": 300}})
    def test_handoff_is_rate_limited_per_visitor(self):
        self.assertEqual(self.handoff().status_code, 302)
        limited = self.handoff()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(ExternalLinkSession.objects.count(), 1)

    def test_handoff_token_never_appears_in_events_or_logs(self):
        token = self.token_from(self.handoff())
        self.validate(token)
        self.force_countdown_finished(token)
        self.redeem(token)
        for event in ExternalLinkEvent.objects.all():
            self.assertNotIn(token, event.message)
            self.assertNotIn(DESTINATION, event.message)
            self.assertNotIn("cdn.nzdworld.com", event.message)
            self.assertNotIn(self.key, event.message)


class ApiAuthenticationTests(ExternalLinkTestBase):
    def payload(self):
        return {"site_url": BLOG_URL, "entry_url_template": ENTRY_TEMPLATE}

    def test_public_api_index_lists_endpoints_without_secrets(self):
        response = self.client.get(reverse("external_links:index"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("connection-test", json.dumps(body))
        self.assertNotIn(self.key, response.content.decode())

    @override_settings(EXTERNAL_LINK_REQUIRE_HTTPS=True)
    def test_https_is_required(self):
        response = self.api("connection_test", self.payload(), secure=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "insecure_transport")

    def test_invalid_api_key_is_rejected_and_audited(self):
        response = self.api("connection_test", self.payload(), key="nzd_live_wrong-key-value")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthorized")
        self.assertTrue(ExternalLinkEvent.objects.filter(event="auth_failed").exists())
        self.assertNotIn("nzd_live_wrong", json.dumps(list(ExternalLinkEvent.objects.values("message"))))

    def test_revoked_key_is_rejected(self):
        service.revoke_api_key(self.config)
        response = self.api("connection_test", self.payload())
        self.assertEqual(response.status_code, 401)

    def test_missing_signature_headers_are_rejected(self):
        response = self.client.post(
            reverse("external_links:connection_test"),
            data=json.dumps(self.payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.key}",
            secure=True,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_signature")

    def test_tampered_body_is_rejected(self):
        signed_body = json.dumps(self.payload())
        sent_body = json.dumps({**self.payload(), "extra": "tampered"})
        nonce = "nonce-value-123"
        timestamp = str(int(timezone.now().timestamp()))
        response = self.api(
            "connection_test",
            None,
            raw_body=sent_body,
            nonce=nonce,
            timestamp=timestamp,
            signature=service.expected_signature(
                self.key, timestamp, nonce, signed_body.encode("utf-8")
            ),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_signature")

    def test_stale_timestamp_is_rejected(self):
        stale = int(timezone.now().timestamp()) - 4000
        response = self.api("connection_test", self.payload(), timestamp=stale)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "stale_request")

    def test_replayed_nonce_is_rejected(self):
        nonce = secrets.token_hex(16)
        first = self.api("connection_test", self.payload(), nonce=nonce)
        self.assertEqual(first.status_code, 200)
        second = self.api("connection_test", self.payload(), nonce=nonce)
        self.assertEqual(second.status_code, 401)
        self.assertEqual(second.json()["error"]["code"], "replayed_request")

    def test_foreign_domain_is_rejected(self):
        response = self.api(
            "connection_test",
            {"site_url": "https://evil.example.test", "entry_url_template": "https://evil.example.test/x/{token}/"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "domain_mismatch")
        self.config.refresh_from_db()
        self.assertEqual(self.config.wordpress_site_url, BLOG_URL)


class CrossImplementationSignatureTests(TestCase):
    """
    The WordPress plugin signs each request with HMAC-SHA256 over
    ``timestamp.nonce.sha256(body)``. This fixed vector was produced
    independently (Python) and is duplicated in the plugin's PHP test harness,
    so both implementations are checked against the same expected digest.
    """

    SECRET = "nzd_live_test_key_ABC123"
    TIMESTAMP = "1700000000"
    NONCE = "abc123def456"
    BODY = '{"site_url":"https://blog.example.com","token":"abc"}'
    EXPECTED = "a66ff43b5e973bedb09598f2ad37b2cd88eed5deb466c865cd77f3b52dc76775"

    def test_expected_signature_matches_the_shared_vector(self):
        self.assertEqual(
            service.expected_signature(self.SECRET, self.TIMESTAMP, self.NONCE, self.BODY.encode("utf-8")),
            self.EXPECTED,
        )


class ConnectionTests(ExternalLinkTestBase):
    def test_first_connection_registers_the_site_and_entry_url(self):
        ExternalLinkConfig.objects.filter(pk=self.config.pk).update(
            wordpress_site_url="", wordpress_entry_template=""
        )
        response = self.api(
            "connection_test",
            {
                "site_url": BLOG_URL,
                "entry_url_template": ENTRY_TEMPLATE,
                "plugin_version": "1.0.0",
                "site_name": "NZDWorld Jobs",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["integration_enabled"])
        self.assertEqual(body["registered_site"], BLOG_URL)

        self.config.refresh_from_db()
        self.assertEqual(self.config.wordpress_site_url, BLOG_URL)
        self.assertEqual(self.config.wordpress_entry_template, ENTRY_TEMPLATE)
        self.assertTrue(self.config.last_connection_ok)
        self.assertEqual(self.config.last_connection_plugin_version, "1.0.0")

    def test_entry_template_on_another_host_is_refused(self):
        response = self.api(
            "connection_test",
            {
                "site_url": BLOG_URL,
                "entry_url_template": "https://evil.example.test/nzdworld-link/{token}/",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_entry_url")

    def test_entry_template_must_contain_the_token_placeholder(self):
        response = self.api(
            "connection_test",
            {"site_url": BLOG_URL, "entry_url_template": "https://blog.example.test/nzdworld-link/"},
        )
        self.assertEqual(response.status_code, 400)

    def test_http_site_urls_are_refused(self):
        response = self.api(
            "connection_test",
            {"site_url": "http://blog.example.test", "entry_url_template": ENTRY_TEMPLATE},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_site")


class SessionLifecycleTests(ExternalLinkTestBase):
    def test_validate_stamps_the_server_side_countdown(self):
        token = self.token_from(self.handoff())
        before = timezone.now()
        response = self.validate(token, countdown=30)
        self.assertEqual(response.status_code, 200)
        session_payload = response.json()["session"]
        self.assertEqual(session_payload["status"], "validated")
        self.assertFalse(session_payload["can_redeem"])
        self.assertGreaterEqual(session_payload["remaining_seconds"], 27)
        self.assertLessEqual(session_payload["remaining_seconds"], 31)

        session = ExternalLinkSession.objects.get()
        self.assertIsNotNone(session.not_before)
        self.assertGreaterEqual(session.not_before, before + timedelta(seconds=25))

    def test_validate_is_idempotent_and_does_not_restart_the_countdown(self):
        token = self.token_from(self.handoff())
        first = self.validate(token, countdown=30).json()["session"]
        stamped = ExternalLinkSession.objects.get().not_before
        # Simulate a refresh a few seconds later: the deadline must not move.
        older = timezone.now() + timedelta(seconds=10)
        ExternalLinkSession.objects.update(not_before=older)
        second = self.validate(token, countdown=30).json()["session"]
        self.assertLessEqual(second["remaining_seconds"], 11)
        self.assertLessEqual(second["remaining_seconds"], first["remaining_seconds"])
        self.assertEqual(ExternalLinkSession.objects.get().not_before, older)
        self.assertLessEqual(older, stamped + timedelta(seconds=1))

    def test_countdown_is_clamped_to_the_configured_maximum(self):
        ExternalLinkConfig.objects.filter(pk=self.config.pk).update(max_countdown_seconds=60)
        token = self.token_from(self.handoff())
        payload = self.validate(token, countdown=9999).json()["session"]
        self.assertEqual(payload["countdown_seconds"], 60)
        self.assertLessEqual(payload["remaining_seconds"], 60)

    def test_countdown_too_long_for_the_session_lifetime_is_refused(self):
        ExternalLinkConfig.objects.filter(pk=self.config.pk).update(
            session_ttl_seconds=120, min_countdown_seconds=5, max_countdown_seconds=600
        )
        token = self.token_from(self.handoff())
        response = self.validate(token, countdown=600)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "countdown_too_long")

    def test_unknown_token_is_refused(self):
        response = self.validate("A" * 43)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "invalid_token")

    def test_expired_session_cannot_be_validated_or_redeemed(self):
        token = self.token_from(self.handoff())
        ExternalLinkSession.objects.update(expires_at=timezone.now() - timedelta(seconds=5))
        validate = self.validate(token)
        self.assertEqual(validate.status_code, 410)
        self.assertEqual(validate.json()["error"]["code"], "session_expired")
        redeem = self.redeem(token)
        self.assertEqual(redeem.status_code, 410)

    def test_redeem_before_the_countdown_is_refused(self):
        token = self.token_from(self.handoff())
        self.validate(token, countdown=30)
        response = self.redeem(token)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["error"]["code"], "too_early")
        self.assertGreaterEqual(body["error"]["retry_after"], 1)
        self.assertNotIn("destination_url", body)
        session = ExternalLinkSession.objects.get()
        self.assertIsNone(session.redeemed_at)

    def test_redeem_requires_validation_first(self):
        token = self.token_from(self.handoff())
        response = self.redeem(token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "session_not_validated")

    def test_redeem_after_the_countdown_releases_the_destination_once(self):
        token = self.token_from(self.handoff())
        self.validate(token, countdown=5)
        self.force_countdown_finished(token)

        response = self.redeem(token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["destination_url"], DESTINATION)
        self.quality.refresh_from_db()
        self.assertEqual(self.quality.download_count, 1)
        session = ExternalLinkSession.objects.get()
        self.assertIsNotNone(session.redeemed_at)
        self.assertEqual(session.status, ExternalLinkSession.Status.REDEEMED)

        replay = self.redeem(token)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()["error"]["code"], "session_redeemed")
        self.quality.refresh_from_db()
        self.assertEqual(self.quality.download_count, 1)

    def test_entry_route_style_revalidation_after_redemption_is_refused(self):
        token = self.token_from(self.handoff())
        self.validate(token)
        self.force_countdown_finished(token)
        self.assertEqual(self.redeem(token).status_code, 200)
        again = self.validate(token)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["code"], "session_redeemed")

    def test_deleted_quality_record_fails_cleanly(self):
        token = self.token_from(self.handoff())
        self.validate(token, countdown=5)
        self.force_countdown_finished(token)
        self.quality.delete()
        response = self.redeem(token)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"]["code"], "quality_unavailable")

    def test_disabled_downloads_fail_cleanly(self):
        token = self.token_from(self.handoff())
        self.validate(token, countdown=5)
        self.force_countdown_finished(token)
        Post.objects.filter(pk=self.post.pk).update(enable_downloads=False)
        response = self.redeem(token)
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"]["code"], "download_disabled")

    def test_session_cannot_be_used_from_another_installation(self):
        token = self.token_from(self.handoff())
        self.validate(token, countdown=5)
        self.force_countdown_finished(token)

        # Administrator re-registers a different WordPress installation.
        service.reset_registered_domain(self.config, keep_key=True)
        registered = self.api(
            "connection_test",
            {
                "site_url": "https://other.example.test",
                "entry_url_template": "https://other.example.test/nzdworld-link/{token}/",
            },
        )
        self.assertEqual(registered.status_code, 200)

        hijack = self.api(
            "session_redeem",
            {"site_url": "https://other.example.test", "token": token},
        )
        self.assertEqual(hijack.status_code, 403)
        self.assertEqual(hijack.json()["error"]["code"], "domain_mismatch")
        self.assertFalse(ExternalLinkSession.objects.get().redeemed_at)

    def test_admin_can_revoke_a_session(self):
        token = self.token_from(self.handoff())
        session = ExternalLinkSession.objects.get()
        ExternalLinkSession.objects.filter(pk=session.pk).update(
            status=ExternalLinkSession.Status.REVOKED
        )
        response = self.validate(token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "session_revoked")


class SessionCreateEndpointTests(ExternalLinkTestBase):
    def test_create_endpoint_returns_a_token(self):
        response = self.api("session_create", {"site_url": BLOG_URL, "quality_id": self.quality.pk})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertRegex(body["token"], service.SESSION_TOKEN_RE)
        self.assertEqual(body["session"]["quality"], "720p")
        self.assertIsNone(body["session"]["not_before"])
        self.assertEqual(ExternalLinkSession.objects.count(), 1)

    def test_create_endpoint_refuses_premium_rows(self):
        DownloadQuality.objects.filter(pk=self.quality.pk).update(is_premium=True)
        response = self.api("session_create", {"site_url": BLOG_URL, "quality_id": self.quality.pk})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "premium_not_allowed")

    def test_create_endpoint_validates_the_quality_id(self):
        self.assertEqual(
            self.api("session_create", {"site_url": BLOG_URL, "quality_id": "abc"}).status_code, 400
        )
        self.assertEqual(
            self.api("session_create", {"site_url": BLOG_URL, "quality_id": 999999}).status_code, 404
        )


class AdminSurfaceTests(ExternalLinkTestBase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.admin = User.objects.create_superuser("root", "root@example.test", "x")

    def test_control_center_page_requires_staff_and_permission(self):
        url = reverse("controlcenter:external_link_generator")
        self.assertEqual(self.client.get(url).status_code, 302)  # login redirect

        self.client.force_login(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"External Link Generator", response.content)

    def test_generate_key_action_shows_the_plaintext_once(self):
        url = reverse("controlcenter:external_link_generator")
        self.client.force_login(self.admin)
        response = self.client.post(url, {"action": "generate_key"})
        self.assertEqual(response.status_code, 302)

        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        match = re.search(r"nzd_live_[A-Za-z0-9_-]{20,}", page.content.decode())
        self.assertIsNotNone(match, "the new key should be displayed once")
        full_key = match.group(0)
        self.assertEqual(service.hash_api_key(full_key), service.get_config().api_key_hash)

        second = self.client.get(url)
        self.assertNotIn(full_key, second.content.decode())

    def test_enable_disable_actions(self):
        url = reverse("controlcenter:external_link_generator")
        self.client.force_login(self.admin)
        self.client.post(url, {"action": "disable"})
        self.config.refresh_from_db()
        self.assertFalse(self.config.enabled)

        self.client.post(url, {"action": "enable"})
        self.config.refresh_from_db()
        self.assertTrue(self.config.enabled)

    def test_settings_form_rejects_unsafe_values(self):
        from core.external_link_forms import ExternalLinkConfigForm

        form = ExternalLinkConfigForm(
            data={
                "enabled": True,
                "wordpress_site_url": "http://blog.example.test",
                "wordpress_entry_template": ENTRY_TEMPLATE,
                "domain_locked": True,
                "session_ttl_seconds": 60,
                "min_countdown_seconds": 5,
                "max_countdown_seconds": 120,
                "api_clock_skew_seconds": 300,
            },
            instance=self.config,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("wordpress_site_url", form.errors)
        self.assertIn("session_ttl_seconds", form.errors)

    def test_django_admin_registration_and_custom_columns(self):
        """
        The private Django admin shares the same ModelAdmin. Rendering admin
        templates through the test client trips a Python 3.14/Django 5.0
        context-copy quirk in this project's test shim, so this checks the
        registration, the available actions and the custom read-only columns
        instead (``manage.py check`` validates the fieldsets themselves).
        """
        from django.contrib import admin
        from django.test import RequestFactory

        model_admin = admin.site._registry[ExternalLinkConfig]
        request = RequestFactory().get("/")
        request.user = self.admin

        actions = model_admin.get_actions(request)
        self.assertIn("action_generate_api_key", actions)
        self.assertIn("action_disable_integration", actions)
        self.assertIn("action_reset_domain", actions)
        self.assertIn("Active", str(model_admin.key_badge(self.config)))
        self.assertIn("Sessions", str(model_admin.statistics_summary(self.config)))
        self.assertIn("Registered site", str(model_admin.connection_summary(self.config)))
        self.assertIn(
            "external-link-generator", str(model_admin.operator_panel(self.config))
        )
        self.assertEqual(model_admin.has_add_permission(request), False)
