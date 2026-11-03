"""
Upload visibility on the post list, and the long-tag 500 regression.

The 500 came from ``form.save_m2m()``: taggit's ``name`` column is VARCHAR(100),
so an over-long tag raised a database error *after* the post row was written and
before the publish hook ran. These tests pin the form-level guard and the new
"Upload" column.
"""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings

from core.admin import PostAdmin, PostAdminForm
from core.models import Category, DownloadQuality, Post

SETTINGS = dict(SECURE_SSL_REDIRECT=False)


@override_settings(**SETTINGS)
class TagLengthValidationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin", "a@example.com", "pw", is_staff=True, is_superuser=True)
        self.category = Category.objects.create(name="Movies", slug="movies")

    def _form(self, tags):
        return PostAdminForm(
            data={
                "title": "Beware Boiṣana (2026)",
                "slug": "beware-boisana-2026",
                "content": "<p>x</p>",
                "excerpt": "",
                "author": self.user.pk,
                "category": self.category.pk,
                "is_published": "",
                "views": "0",
                "enable_downloads": "on",
                "tags": tags,
            }
        )

    def test_an_over_long_tag_is_a_form_error_not_a_500(self):
        form = self._form("x" * 150)
        self.assertFalse(form.is_valid())
        self.assertIn("longer than 100 characters", str(form.errors))

    def test_normal_tags_still_pass(self):
        form = self._form("action, 2026, nollywood")
        self.assertTrue(form.is_valid(), form.errors)

    def test_many_short_tags_are_fine(self):
        form = self._form(", ".join(f"tag{i}" for i in range(40)))
        self.assertTrue(form.is_valid(), form.errors)

    def test_newlines_are_treated_as_separators(self):
        form = self._form("ok\n" + "y" * 120)
        self.assertFalse(form.is_valid())


@override_settings(**SETTINGS)
class UploadStatusColumnTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("admin2", "b@example.com", "pw", is_staff=True, is_superuser=True)
        self.category = Category.objects.create(name="Series", slug="series")
        self.modeladmin = PostAdmin(Post, admin.site)
        self.request = RequestFactory().get("/control-center/core/post/")
        self.request.user = self.user

    def post(self, title="Tulsa King"):
        return Post.objects.create(
            title=title, slug=title.lower().replace(" ", "-"), content="x",
            author=self.user, category=self.category,
        )

    def row(self, post, quality, status):
        return DownloadQuality.objects.create(
            post=post, quality=quality, download_url="https://host.example/a.mkv",
            processing_status=status,
        )

    def test_no_rows_shows_a_dash(self):
        self.assertIn("—", str(self.modeladmin.upload_status(self.post())))

    def test_in_progress_is_visible(self):
        post = self.post()
        self.row(post, "720p", DownloadQuality.ProcessingStatus.DOWNLOADING)
        self.row(post, "1080p", DownloadQuality.ProcessingStatus.COMPLETED)
        html = str(self.modeladmin.upload_status(post))
        self.assertIn("uploading 1/2", html)
        self.assertIn("#1d4ed8", html)

    def test_failure_is_visible_and_explained(self):
        post = self.post()
        self.row(post, "720p", DownloadQuality.ProcessingStatus.COMPLETED)
        failed = self.row(post, "1080p", DownloadQuality.ProcessingStatus.FAILED)
        failed.processing_error = "http_404: gone"
        failed.save(update_fields=["processing_error"])
        html = str(self.modeladmin.upload_status(post))
        self.assertIn("1 failed", html)
        self.assertIn("http_404", html)

    def test_all_complete_shows_success(self):
        post = self.post()
        self.row(post, "720p", DownloadQuality.ProcessingStatus.COMPLETED)
        self.assertIn("✓ 1/1", str(self.modeladmin.upload_status(post)))

    def test_the_list_page_shows_the_column(self):
        post = self.post()
        self.row(post, "720p", DownloadQuality.ProcessingStatus.QUEUED)
        self.client.force_login(self.user)
        response = self.client.get("/control-center/core/post/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Upload", body)
        self.assertIn("uploading 0/1", body)

    def test_queryset_prefetches_qualities(self):
        queryset = self.modeladmin.get_queryset(self.request)
        self.assertIn("qualities", queryset._prefetch_related_lookups)

    def test_progress_percentage_is_shown_while_active(self):
        post = self.post()
        row = self.row(post, "720p", DownloadQuality.ProcessingStatus.DOWNLOADING)
        row.progress_percent = 45
        row.save(update_fields=["progress_percent"])
        html = str(self.modeladmin.upload_status(post))
        self.assertIn("45%", html)
        self.assertIn("uploading", html)