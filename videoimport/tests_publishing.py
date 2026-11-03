"""
Publish-workflow tests: per-row filenames, draft/publish split, batch gating.

The uploader API is always mocked here -- these tests prove the orchestration
and the payload, not the network.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings

from core.models import Category, DownloadQuality, Post
from videoimport import client, publishing
from videoimport.models import UploadBatch

API_SETTINGS = dict(
    UPLOADER_API_URL="https://my.nzdworld.com",
    UPLOADER_API_KEY_ID="k",
    UPLOADER_API_SECRET="s",
    UPLOADER_ENABLED=True,
    SECURE_SSL_REDIRECT=False,
)


@override_settings(**API_SETTINGS)
class PublishBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", "a@example.com", "pw", is_staff=True)
        self.category = Category.objects.create(name="Movies", slug="movies")
        self.post = Post.objects.create(
            title="The Black Book 2026",
            slug="the-black-book-2026",
            content="x",
            author=self.user,
            category=self.category,
            is_published=False,
        )

    def row(self, **kwargs):
        defaults = dict(
            post=self.post,
            quality="720p",
            download_url="https://host.example/The.Black.Book.2026.mkv",
            filename_mode=DownloadQuality.FilenameMode.AUTO,
        )
        defaults.update(kwargs)
        return DownloadQuality.objects.create(**defaults)

    def job(self, job_id="job1", status="queued"):
        return {"job": {"id": job_id, "status": status}}


class QueueTests(PublishBase):
    def test_publish_creates_a_batch_and_queues_each_row(self):
        first = self.row(quality="720p")
        second = self.row(quality="1080p", download_url="https://host.example/b.mkv")

        with mock.patch.object(client, "create_import", side_effect=[self.job("j1"), self.job("j2")]) as create:
            batch = publishing.queue_publish(self.post)

        self.assertEqual(batch.status, UploadBatch.Status.PROCESSING)
        self.assertEqual(create.call_count, 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.job_public_id, "j1")
        self.assertEqual(second.job_public_id, "j2")
        self.assertEqual(first.processing_status, DownloadQuality.ProcessingStatus.QUEUED)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_published)  # stays a draft while uploading

    def test_automatic_title_includes_the_quality_label(self):
        self.row(quality="1080p")
        with mock.patch.object(client, "create_import", return_value=self.job()) as create:
            publishing.queue_publish(self.post)
        self.assertIn("1080p FHD", create.call_args.kwargs["title"])

    def test_manual_filename_is_passed_to_the_existing_api(self):
        self.row(
            quality="EP3",
            filename_mode=DownloadQuality.FilenameMode.MANUAL,
            requested_filename="Series-Name-S01E03.mp4",
        )
        with mock.patch.object(client, "create_import", return_value=self.job()) as create:
            publishing.queue_publish(self.post)
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["filename_mode"], "manual")
        self.assertEqual(kwargs["requested_filename"], "Series-Name-S01E03.mp4")

    def test_rows_without_a_url_are_not_queued(self):
        self.row(download_url="", source_url="")
        with mock.patch.object(client, "create_import") as create:
            batch = publishing.queue_publish(self.post)
        create.assert_not_called()
        self.assertEqual(batch.status, UploadBatch.Status.FAILED)
        self.assertIn("No download rows", batch.error)

    def test_already_processed_rows_are_skipped(self):
        self.row(
            processing_status=DownloadQuality.ProcessingStatus.COMPLETED,
            download_url="https://my.nzdworld.com/d/abc/",
            source_url="https://host.example/a.mkv",
        )
        with mock.patch.object(client, "create_import") as create:
            batch = publishing.queue_publish(self.post)
        create.assert_not_called()
        self.assertEqual(batch.status, UploadBatch.Status.FAILED)

    def test_uploader_error_marks_the_row_failed(self):
        self.row()
        with mock.patch.object(
            client, "create_import", side_effect=client.UploaderError("unsafe_url", "blocked")
        ):
            batch = publishing.queue_publish(self.post)
        row = self.post.qualities.first()
        self.assertEqual(row.processing_status, DownloadQuality.ProcessingStatus.FAILED)
        self.assertIn("unsafe_url", row.processing_error)
        self.assertEqual(batch.status, UploadBatch.Status.FAILED)

    def test_duplicate_job_is_adopted_not_failed(self):
        self.row()
        error = client.UploaderError("duplicate_import", "already queued", payload={"job": {"id": "existing"}})
        with mock.patch.object(client, "create_import", side_effect=error):
            publishing.queue_publish(self.post)
        row = self.post.qualities.first()
        self.assertEqual(row.job_public_id, "existing")
        self.assertEqual(row.processing_status, DownloadQuality.ProcessingStatus.QUEUED)


class SyncTests(PublishBase):
    def test_all_rows_completed_publishes_the_post(self):
        row = self.row()
        with mock.patch.object(client, "create_import", return_value=self.job("j1")):
            batch = publishing.queue_publish(self.post)

        completed = {
            "job": {
                "id": "j1",
                "status": "completed",
                "progress_percent": 100,
                "video": {"download_page_url": "https://my.nzdworld.com/d/xyz/"},
            }
        }
        with mock.patch.object(client, "import_status", return_value=completed):
            batch = publishing.sync_batch(batch)

        row.refresh_from_db()
        self.post.refresh_from_db()
        self.assertEqual(row.processing_status, DownloadQuality.ProcessingStatus.COMPLETED)
        self.assertEqual(row.download_url, "https://my.nzdworld.com/d/xyz/")
        self.assertTrue(self.post.is_published)
        self.assertEqual(batch.status, UploadBatch.Status.COMPLETED)
        self.assertIsNotNone(batch.published_at)

    def test_a_failed_row_keeps_the_post_as_draft(self):
        self.row()
        with mock.patch.object(client, "create_import", return_value=self.job("j1")):
            batch = publishing.queue_publish(self.post)
        failed = {"job": {"id": "j1", "status": "failed", "error": {"code": "http_404", "message": "gone"}}}
        with mock.patch.object(client, "import_status", return_value=failed):
            batch = publishing.sync_batch(batch)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_published)
        self.assertEqual(batch.status, UploadBatch.Status.FAILED)
        self.assertIn("http_404", self.post.qualities.first().processing_error)

    def test_partial_progress_does_not_publish(self):
        self.row()
        with mock.patch.object(client, "create_import", return_value=self.job("j1")):
            batch = publishing.queue_publish(self.post)
        running = {"job": {"id": "j1", "status": "transferring", "progress_percent": 45}}
        with mock.patch.object(client, "import_status", return_value=running):
            batch = publishing.sync_batch(batch)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_published)
        self.assertEqual(batch.status, UploadBatch.Status.PROCESSING)
        self.assertEqual(self.post.qualities.first().progress_percent, 45)

    def test_an_older_batch_cannot_publish(self):
        row = self.row()
        with mock.patch.object(client, "create_import", return_value=self.job("j1")):
            old = publishing.queue_publish(self.post)
        # A second publish creates a newer version; the old one must not publish.
        with mock.patch.object(client, "create_import", return_value=self.job("j2")):
            newer = publishing.queue_publish(self.post)
        self.assertGreater(newer.version, old.version)

        completed = {"job": {"id": "j1", "status": "completed", "progress_percent": 100,
                             "video": {"download_page_url": "https://my.nzdworld.com/d/old/"}}}
        with mock.patch.object(client, "import_status", return_value=completed):
            publishing.sync_batch(old)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_published)

    def test_sync_all_processes_active_batches(self):
        self.row()
        with mock.patch.object(client, "create_import", return_value=self.job("j1")):
            publishing.queue_publish(self.post)
        completed = {"job": {"id": "j1", "status": "completed", "progress_percent": 100,
                             "video": {"download_page_url": "https://my.nzdworld.com/d/ok/"}}}
        with mock.patch.object(client, "import_status", return_value=completed):
            result = publishing.sync_all()
        self.assertEqual(result["completed"], 1)


class RetryTests(PublishBase):
    def test_retry_reuses_the_existing_job(self):
        row = self.row(processing_status=DownloadQuality.ProcessingStatus.FAILED, job_public_id="j1")
        with mock.patch.object(client, "retry_import") as retry:
            publishing.retry_row(row)
        retry.assert_called_once_with("j1")
        row.refresh_from_db()
        self.assertEqual(row.processing_status, DownloadQuality.ProcessingStatus.QUEUED)

    def test_retry_queues_a_new_job_when_the_old_one_is_gone(self):
        row = self.row(
            processing_status=DownloadQuality.ProcessingStatus.FAILED,
            job_public_id="gone",
            source_url="https://host.example/a.mkv",
        )
        with mock.patch.object(
            client, "retry_import", side_effect=client.UploaderError("not_retryable", "gone")
        ), mock.patch.object(client, "create_import", return_value=self.job("fresh")) as create:
            publishing.retry_row(row)
        create.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.job_public_id, "fresh")


class DraftVsPublishTests(PublishBase):
    """A normal save must never queue an upload."""

    def test_helper_does_nothing_without_the_publish_flag(self):
        from controlcenter.views import _maybe_queue_uploads

        factory = RequestFactory()
        request = factory.post("/", {})
        with mock.patch.object(client, "create_import") as create:
            _maybe_queue_uploads(request, self.post, "core", "post")
        create.assert_not_called()
        self.assertEqual(UploadBatch.objects.count(), 0)

    def test_helper_runs_only_for_posts(self):
        from controlcenter.views import _maybe_queue_uploads

        factory = RequestFactory()
        request = factory.post("/", {"_nzd_publish": "1"})
        with mock.patch.object(client, "create_import") as create:
            _maybe_queue_uploads(request, self.category, "core", "category")
        create.assert_not_called()

    def test_flag_triggers_queueing(self):
        from controlcenter.views import _maybe_queue_uploads

        self.row()
        factory = RequestFactory()
        request = factory.post("/", {"_nzd_publish": "1"})
        with mock.patch.object(client, "create_import", return_value=self.job()) as create:
            _maybe_queue_uploads(request, self.post, "core", "post")
        create.assert_called_once()


@override_settings(**API_SETTINGS)
class PanelEndpointTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("s", "s@example.com", "pw", is_staff=True, is_superuser=True)
        self.client.force_login(self.staff)

    def test_verify_proxies_the_uploader(self):
        with mock.patch.object(
            client, "verify_url", return_value={"status": "valid", "reason": "ok"}
        ):
            response = self.client.post(
                "/control-center/video-import/verify/",
                data=b'{"url": "https://host.example/a.mkv"}',
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "valid")

    def test_row_preview_uses_the_post_title_and_quality(self):
        with mock.patch.object(
            client,
            "preview_filename",
            return_value={"auto": "the-black-book-2026-720p-hd | NZDWorld.com.mkv", "manual": ""},
        ) as preview:
            response = self.client.post(
                "/control-center/video-import/row-preview/",
                data=b'{"url": "https://host.example/a.mkv", "quality": "720p", "title": "The Black Book 2026"}',
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("720p HD", preview.call_args.kwargs["title"])

    def test_batch_status_returns_rows(self):
        user = self.staff
        post = Post.objects.create(
            title="P", slug="p", content="x", author=user,
            category=Category.objects.create(name="C", slug="c"),
        )
        DownloadQuality.objects.create(post=post, quality="720p", download_url="https://e.com/a.mkv")
        response = self.client.get(f"/control-center/video-import/batch-status/{post.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["rows"]), 1)


@override_settings(**API_SETTINGS)
class UiDrivenSyncTests(PublishBase):
    """The UI must move progress forward even when the cron command is absent."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _queued(self, job_id="j1"):
        self.row()
        with mock.patch.object(client, "create_import", return_value=self.job(job_id)):
            return publishing.queue_publish(self.post)

    def test_batch_status_endpoint_polls_and_publishes(self):
        self._queued()
        completed = {
            "job": {
                "id": "j1",
                "status": "completed",
                "progress_percent": 100,
                "video": {"download_page_url": "https://my.nzdworld.com/d/abc/"},
            }
        }
        with mock.patch.object(client, "import_status", return_value=completed) as status:
            response = self.client.get(
                f"/control-center/video-import/batch-status/{self.post.pk}/"
            )
        self.assertEqual(response.status_code, 200)
        status.assert_called()
        payload = response.json()
        self.assertEqual(payload["rows"][0]["status"], "completed")

        self.post.refresh_from_db()
        self.assertTrue(self.post.is_published)
        row = self.post.qualities.first()
        self.assertEqual(row.download_url, "https://my.nzdworld.com/d/abc/")

    def test_batch_status_reports_progress_while_downloading(self):
        self._queued()
        running = {"job": {"id": "j1", "status": "downloading", "progress_percent": 42}}
        with mock.patch.object(client, "import_status", return_value=running):
            payload = self.client.get(
                f"/control-center/video-import/batch-status/{self.post.pk}/"
            ).json()
        self.assertEqual(payload["rows"][0]["status"], "downloading")
        self.assertEqual(payload["rows"][0]["progress"], 42)
        self.post.refresh_from_db()
        self.assertFalse(self.post.is_published)

    def test_sync_for_post_is_throttled(self):
        from django.core.cache import cache

        cache.clear()
        self._queued()
        running = {"job": {"id": "j1", "status": "downloading", "progress_percent": 10}}
        with mock.patch.object(client, "import_status", return_value=running) as status:
            publishing.sync_for_post(self.post, throttle_seconds=60)
            publishing.sync_for_post(self.post, throttle_seconds=60)
        self.assertEqual(status.call_count, 1)

    def test_sync_page_touches_only_posts_with_active_batches(self):
        from django.core.cache import cache
        from videoimport.models import UploadBatch

        cache.clear()
        self._queued()
        other = Post.objects.create(
            title="No uploads", slug="no-uploads", content="x",
            author=self.user, category=self.category, is_published=False,
        )
        running = {"job": {"id": "j1", "status": "downloading", "progress_percent": 5}}
        with mock.patch.object(client, "import_status", return_value=running) as status:
            synced = publishing.sync_page([self.post, other], throttle_seconds=0)
        self.assertEqual(synced, 1)
        self.assertEqual(status.call_count, 1)
        self.assertFalse(UploadBatch.objects.filter(post=other).exists())

    def test_row_queued_without_a_job_id_is_failed_not_left_hanging(self):
        batch = UploadBatch.objects.create(post=self.post, version=1)
        row = self.row(processing_status=DownloadQuality.ProcessingStatus.QUEUED)
        row.job_public_id = ""
        row.save(update_fields=["job_public_id"])
        batch = publishing.sync_batch(batch)
        row.refresh_from_db()
        self.assertEqual(row.processing_status, DownloadQuality.ProcessingStatus.FAILED)
        self.assertIn("Not queued", row.processing_error)
        self.assertEqual(batch.status, UploadBatch.Status.FAILED)