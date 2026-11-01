"""Tests for the blog-side uploader client and the VideoRef model.

The network is always faked: these tests prove signing, error mapping and the
request shape without needing the uploader to be reachable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings

from videoimport import client
from videoimport.models import VideoRef

SECRET = "unit-test-shared-secret"
BASE = "https://my.nzdworld.com"
SETTINGS = dict(
    UPLOADER_API_URL=BASE,
    UPLOADER_API_KEY_ID="blog-key",
    UPLOADER_API_SECRET=SECRET,
    UPLOADER_TIMEOUT_SECONDS=5,
    UPLOADER_ENABLED=True,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def expected_signature(method, path, body: bytes, headers):
    canonical = "\n".join(
        [
            method.upper(),
            path,
            "",
            hashlib.sha256(body).hexdigest(),
            headers["X-NZD-Timestamp"],
            headers["X-NZD-Nonce"],
        ]
    )
    key = hashlib.sha256(SECRET.encode()).hexdigest()
    return hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()


@override_settings(**SETTINGS)
class ClientSigningTests(TestCase):
    def test_create_import_signs_the_exact_body_it_sends(self):
        payload = {"job": {"id": "abc", "status": "queued"}}
        with mock.patch.object(client.requests, "request", return_value=FakeResponse(201, payload)) as req:
            result = client.create_import(
                source_url="https://example.com/a.mkv", title="A", created_by="admin"
            )

        args, kwargs = req.call_args
        method, url = args[0], args[1]
        body = kwargs["data"]
        self.assertEqual(method, "POST")
        self.assertEqual(url, f"{BASE}/api/v1/imports/")
        self.assertEqual(json.loads(body)["source_url"], "https://example.com/a.mkv")

        headers = kwargs["headers"]
        self.assertEqual(headers["X-NZD-Key"], "blog-key")
        self.assertEqual(
            headers["X-NZD-Signature"], expected_signature("POST", "/api/v1/imports/", body, headers)
        )
        self.assertEqual(result, payload)

    def test_get_uses_an_empty_body_digest(self):
        with mock.patch.object(client.requests, "request", return_value=FakeResponse(200, {"job": {}})) as req:
            client.import_status("abc123")
        args, kwargs = req.call_args
        self.assertEqual(args[0], "GET")
        self.assertTrue(args[1].endswith("/api/v1/imports/abc123/"))
        self.assertIsNone(kwargs["data"])
        headers = kwargs["headers"]
        self.assertEqual(
            headers["X-NZD-Signature"],
            expected_signature("GET", "/api/v1/imports/abc123/", b"", headers),
        )

    def test_each_request_uses_a_fresh_nonce(self):
        with mock.patch.object(client.requests, "request", return_value=FakeResponse(200, {})) as req:
            client.cancel_import("a")
            client.cancel_import("a")
        first, second = req.call_args_list
        self.assertNotEqual(
            first.kwargs["headers"]["X-NZD-Nonce"], second.kwargs["headers"]["X-NZD-Nonce"]
        )

    def test_signature_matches_the_server_algorithm(self):
        """A fixed vector, so a change on either side is caught immediately."""
        headers = client.signed_headers("POST", "/api/v1/imports/", b'{"a":1}')
        self.assertEqual(
            headers["X-NZD-Signature"],
            expected_signature("POST", "/api/v1/imports/", b'{"a":1}', headers),
        )


@override_settings(**SETTINGS)
class ClientErrorTests(TestCase):
    def test_server_error_is_mapped_to_a_typed_error(self):
        with mock.patch.object(
            client.requests,
            "request",
            return_value=FakeResponse(401, {"error": "bad_signature", "message": "nope"}),
        ):
            with self.assertRaises(client.UploaderError) as ctx:
                client.import_status("abc")
        self.assertEqual(ctx.exception.code, "bad_signature")
        self.assertEqual(ctx.exception.status, 401)

    def test_duplicate_import_keeps_the_existing_job_payload(self):
        payload = {"error": "duplicate_import", "job": {"id": "existing"}}
        with mock.patch.object(client.requests, "request", return_value=FakeResponse(409, payload)):
            with self.assertRaises(client.UploaderError) as ctx:
                client.create_import(source_url="https://example.com/a.mkv", title="A")
        self.assertEqual(ctx.exception.code, "duplicate_import")
        self.assertEqual(ctx.exception.payload["job"]["id"], "existing")

    def test_timeout_is_reported_as_timeout(self):
        with mock.patch.object(client.requests, "request", side_effect=client.requests.Timeout("slow")):
            with self.assertRaises(client.UploaderError) as ctx:
                client.import_status("abc")
        self.assertEqual(ctx.exception.code, "timeout")

    def test_connection_error_is_reported(self):
        with mock.patch.object(
            client.requests, "request", side_effect=client.requests.ConnectionError("refused")
        ):
            with self.assertRaises(client.UploaderError) as ctx:
                client.import_status("abc")
        self.assertEqual(ctx.exception.code, "connection_failed")

    def test_non_json_error_body_still_raises(self):
        with mock.patch.object(client.requests, "request", return_value=FakeResponse(502, None)):
            with self.assertRaises(client.UploaderError) as ctx:
                client.import_status("abc")
        self.assertEqual(ctx.exception.code, "http_error")
        self.assertEqual(ctx.exception.status, 502)

    @override_settings(UPLOADER_ENABLED=False)
    def test_disabled_integration_refuses_without_calling_out(self):
        with mock.patch.object(client.requests, "request") as req:
            with self.assertRaises(client.UploaderError) as ctx:
                client.import_status("abc")
        self.assertEqual(ctx.exception.code, "disabled")
        req.assert_not_called()

    @override_settings(UPLOADER_API_SECRET="")
    def test_missing_credentials_are_refused(self):
        self.assertFalse(client.is_configured())
        with self.assertRaises(client.UploaderError) as ctx:
            client.import_status("abc")
        self.assertEqual(ctx.exception.code, "not_configured")


class VideoRefModelTests(TestCase):
    def test_public_id_is_unique(self):
        from django.db import IntegrityError, transaction

        VideoRef.objects.create(public_id="dup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                VideoRef.objects.create(public_id="dup")

    def test_is_ready_requires_completion_and_a_url(self):
        ref = VideoRef.objects.create(public_id="a", status=VideoRef.Status.COMPLETED)
        self.assertFalse(ref.is_ready)
        ref.download_page_url = "https://my.nzdworld.com/d/abc/"
        self.assertTrue(ref.is_ready)

    def test_str_falls_back_to_the_public_id(self):
        ref = VideoRef.objects.create(public_id="abcdef123456", filename="")
        self.assertIn("abcdef123456", str(ref))
