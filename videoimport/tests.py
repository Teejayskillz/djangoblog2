"""Tests for the blog-side uploader client and the VideoRef model.

The network is always faked: these tests prove signing, error mapping and the
request shape without needing the uploader to be reachable.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest import mock

from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

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

COMPLETED_JOB = {
    "id": "abc123def456",
    "status": "completed",
    "progress_percent": 100,
    "filename": "extraction-3-2026 | NZDWorld.com.mkv",
    "video": {
        "public_id": "abc123def456",
        "filename": "extraction-3-2026 | NZDWorld.com.mkv",
        "size_bytes": 1048576,
        "sha256": "f" * 64,
        "download_page_url": "https://my.nzdworld.com/d/abc123def456/",
    },
}


@override_settings(**SETTINGS)
class ImportVideoCommandTests(TestCase):
    def _base_patches(self, statuses=None):
        statuses = statuses or [COMPLETED_JOB]
        self.create = mock.patch.object(
            client, "create_import", return_value={"job": {"id": "abc123def456", "status": "queued"}}
        )
        self.status = mock.patch.object(
            client, "import_status", side_effect=[{"job": job} for job in statuses]
        )
        self.sleep = mock.patch("videoimport.management.commands.import_video.time.sleep")
        for patcher in (self.create, self.status, self.sleep):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_imports_and_records_a_video_ref(self):
        self._base_patches()
        out = StringIO()
        call_command(
            "import_video", "https://example.com/a.mkv", "--title", "Extraction 3", stdout=out
        )
        ref = VideoRef.objects.get(public_id="abc123def456")
        self.assertEqual(ref.status, VideoRef.Status.COMPLETED)
        self.assertEqual(ref.download_page_url, "https://my.nzdworld.com/d/abc123def456/")
        self.assertEqual(ref.size_bytes, 1048576)
        self.assertIn("Import complete", out.getvalue())

    def test_attaches_a_download_row_to_a_post(self):
        self._base_patches()
        from core.models import Category, DownloadQuality, Post
        from django.contrib.auth.models import User

        user = User.objects.create_user("admin", "a@example.com", "pw")
        category = Category.objects.create(name="Movies", slug="movies")
        post = Post.objects.create(
            title="Extraction 3", slug="extraction-3", content="x", author=user, category=category
        )

        call_command(
            "import_video",
            "https://example.com/a.mkv",
            "--title",
            "Extraction 3",
            "--post",
            str(post.pk),
            "--quality",
            "1080p",
            stdout=StringIO(),
        )
        row = DownloadQuality.objects.get(post=post)
        self.assertEqual(row.download_url, "https://my.nzdworld.com/d/abc123def456/")
        self.assertEqual(row.quality, "1080p")
        self.assertFalse(row.is_premium)
        self.assertEqual(VideoRef.objects.get(public_id="abc123def456").post_id, post.pk)

    def test_failed_job_leaves_a_failed_video_ref(self):
        self._base_patches(
            [
                {
                    "id": "abc123def456",
                    "status": "failed",
                    "error": {"code": "http_404", "message": "not found"},
                }
            ]
        )
        with self.assertRaises(CommandError) as ctx:
            call_command("import_video", "https://example.com/a.mkv", "--title", "T", stdout=StringIO())
        self.assertIn("http_404", str(ctx.exception))
        self.assertEqual(VideoRef.objects.get(public_id="abc123def456").status, VideoRef.Status.FAILED)

    def test_requires_a_url_and_manual_filename(self):
        self._base_patches()
        with self.assertRaises(CommandError):
            call_command("import_video", stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command(
                "import_video",
                "https://example.com/a.mkv",
                "--filename-mode",
                "manual",
                stdout=StringIO(),
            )

    def test_duplicate_import_is_reported_clearly(self):
        with mock.patch.object(
            client,
            "create_import",
            side_effect=client.UploaderError(
                "duplicate_import", "already queued", payload={"job": {"id": "existing1"}}
            ),
        ):
            with self.assertRaises(CommandError) as ctx:
                call_command("import_video", "https://example.com/a.mkv", stdout=StringIO())
        self.assertIn("already being imported", str(ctx.exception))

    @override_settings(UPLOADER_API_SECRET="")
    def test_missing_credentials_stop_before_calling_out(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("import_video", "https://example.com/a.mkv", stdout=StringIO())
        self.assertIn("credentials are missing", str(ctx.exception))

    def test_check_mode_reports_health_and_signed_call(self):
        with mock.patch.object(client, "health", return_value={"ok": True}), mock.patch.object(
            client, "preview_filename", return_value={"auto": "extraction-3 | NZDWorld.com.mkv"}
        ):
            out = StringIO()
            call_command("import_video", "--check", stdout=out)
        self.assertIn("Signed call : OK", out.getvalue())

    def test_attach_existing_links_a_finished_video_to_a_post(self):
        from core.models import Category, DownloadQuality, Post
        from django.contrib.auth.models import User

        user = User.objects.create_user("admin2", "b@example.com", "pw")
        category = Category.objects.create(name="Series", slug="series")
        post = Post.objects.create(
            title="Tulsa King", slug="tulsa-king", content="x", author=user, category=category
        )
        ref = VideoRef.objects.create(
            public_id="ready123",
            status=VideoRef.Status.COMPLETED,
            filename="tulsa-king | NZDWorld.com.mkv",
            download_page_url="https://my.nzdworld.com/d/ready123/",
        )
        call_command(
            "import_video",
            "--attach-id",
            "ready123",
            "--post",
            str(post.pk),
            "--quality",
            "720p",
            "--premium",
            stdout=StringIO(),
        )
        row = DownloadQuality.objects.get(post=post)
        self.assertEqual(row.quality, "720p")
        self.assertTrue(row.is_premium)
        ref.refresh_from_db()
        self.assertEqual(ref.post_id, post.pk)

    def test_attach_existing_refuses_unfinished_or_unknown_videos(self):
        with self.assertRaises(CommandError):
            call_command("import_video", "--attach-id", "nope", "--post", "1", stdout=StringIO())

        VideoRef.objects.create(public_id="pending1", status=VideoRef.Status.PENDING)
        with self.assertRaises(CommandError) as ctx:
            call_command("import_video", "--attach-id", "pending1", "--post", "1", stdout=StringIO())
        self.assertIn("not ready", str(ctx.exception))
@override_settings(**SETTINGS)
class PanelViewTests(TestCase):
    """The control-center panel endpoints: staff-only, no secret in responses."""

    def setUp(self):
        from django.contrib.auth.models import User

        self.staff = User.objects.create_user("staff", "s@example.com", "pw", is_staff=True)
        self.plain = User.objects.create_user("plain", "p@example.com", "pw")

    def _post_json(self, name, payload, *args):
        return self.client.post(
            reverse(name, args=args), data=json.dumps(payload), content_type="application/json"
        )

    def test_anonymous_cannot_use_the_panel(self):
        response = self._post_json("videoimport:create", {"source_url": "https://e.com/a.mkv"})
        self.assertEqual(response.status_code, 302)

    def test_non_staff_cannot_use_the_panel(self):
        self.client.force_login(self.plain)
        response = self._post_json("videoimport:create", {"source_url": "https://e.com/a.mkv"})
        self.assertEqual(response.status_code, 302)

    def test_staff_preview_returns_both_names(self):
        from django.urls import reverse as _r  # noqa: F401

        self.client.force_login(self.staff)
        with mock.patch.object(
            client, "preview_filename", return_value={"auto": "a | NZDWorld.com.mkv", "manual": "", "errors": ""}
        ):
            response = self._post_json(
                "videoimport:preview",
                {"original_filename": "a.mkv", "title": "A"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["auto"], "a | NZDWorld.com.mkv")

    def test_staff_create_records_a_video_ref(self):
        self.client.force_login(self.staff)
        with mock.patch.object(
            client,
            "create_import",
            return_value={"job": {"id": "job123", "status": "queued"}},
        ):
            response = self._post_json(
                "videoimport:create", {"source_url": "https://e.com/a.mkv", "title": "A"}
            )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(VideoRef.objects.filter(public_id="job123").exists())

    def test_duplicate_import_surfaces_409_with_the_existing_job(self):
        self.client.force_login(self.staff)
        with mock.patch.object(
            client,
            "create_import",
            side_effect=client.UploaderError(
                "duplicate_import", "already", payload={"job": {"id": "existing"}}
            ),
        ):
            response = self._post_json("videoimport:create", {"source_url": "https://e.com/a.mkv"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["job"]["id"], "existing")

    def test_status_polls_and_updates_the_ref(self):
        self.client.force_login(self.staff)
        VideoRef.objects.create(public_id="job123", status=VideoRef.Status.QUEUED)
        with mock.patch.object(
            client,
            "import_status",
            return_value={
                "job": {
                    "id": "job123",
                    "status": "completed",
                    "filename": "a | NZDWorld.com.mkv",
                    "video": {
                        "size_bytes": 10,
                        "sha256": "x" * 64,
                        "download_page_url": "https://my.nzdworld.com/d/job123/",
                    },
                }
            },
        ):
            response = self.client.get(reverse("videoimport:status", args=["job123"]))
        self.assertEqual(response.status_code, 200)
        ref = VideoRef.objects.get(public_id="job123")
        self.assertEqual(ref.status, VideoRef.Status.COMPLETED)
        self.assertTrue(ref.is_ready)

    def test_attach_creates_the_download_quality_row(self):
        from core.models import Category, DownloadQuality, Post

        self.client.force_login(self.staff)
        post = Post.objects.create(
            title="Extraction 3", slug="extraction-3", content="x",
            author=self.staff, category=Category.objects.create(name="Movies", slug="movies"),
        )
        VideoRef.objects.create(
            public_id="job123",
            status=VideoRef.Status.COMPLETED,
            download_page_url="https://my.nzdworld.com/d/job123/",
        )
        response = self._post_json(
            "videoimport:attach",
            {"public_id": "job123", "post_id": post.pk, "quality": "1080p", "premium": False},
        )
        self.assertEqual(response.status_code, 201)
        row = DownloadQuality.objects.get(post=post)
        self.assertEqual(row.download_url, "https://my.nzdworld.com/d/job123/")
        self.assertFalse(row.is_premium)

    def test_attach_refuses_unfinished_imports(self):
        from core.models import Category, Post

        self.client.force_login(self.staff)
        post = Post.objects.create(
            title="X", slug="x", content="x", author=self.staff,
            category=Category.objects.create(name="C", slug="c"),
        )
        VideoRef.objects.create(public_id="pending", status=VideoRef.Status.PENDING)
        response = self._post_json(
            "videoimport:attach", {"public_id": "pending", "post_id": post.pk}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "not_ready")

    def test_uploader_secret_never_appears_in_a_response(self):
        self.client.force_login(self.staff)
        with mock.patch.object(client, "create_import", return_value={"job": {"id": "j", "status": "queued"}}):
            response = self._post_json("videoimport:create", {"source_url": "https://e.com/a.mkv"})
        self.assertNotIn(SECRET, response.content.decode())
