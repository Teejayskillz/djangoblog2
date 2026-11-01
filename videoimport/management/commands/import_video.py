"""
Import one video from a URL, straight from the blog.

    python manage.py import_video https://example.com/movie.mp4 --title "Extraction 3"

    # attach the finished video to an existing post as a download row
    python manage.py import_video https://example.com/movie.mkv \
        --title "Extraction 3" --post 123 --quality 1080p

    # only check credentials/connectivity, create nothing
    python manage.py import_video --check

This is the headless version of the control-center import panel: it does the
same HMAC-signed call, polls the uploader, stores a VideoRef, and (with --post)
creates the DownloadQuality row that the post template renders.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from videoimport import client
from videoimport.models import VideoRef

POLL_SECONDS = 5.0


class Command(BaseCommand):
    help = "Import a video from a URL via the storage service and optionally attach it to a post."

    def add_arguments(self, parser):
        parser.add_argument("source_url", nargs="?", help="Direct http(s) video URL (mp4/mkv/webm).")
        parser.add_argument("--title", default="", help="Movie title used for the branded filename.")
        parser.add_argument(
            "--filename-mode",
            choices=["auto", "manual"],
            default="auto",
            help="auto builds the name from --title; manual uses --filename.",
        )
        parser.add_argument("--filename", default="", help="Manual filename (with --filename-mode manual).")
        parser.add_argument("--post", type=int, default=0, help="Post id to attach the download row to.")
        parser.add_argument("--quality", default="1080p", help="DownloadQuality choice (default 1080p).")
        parser.add_argument("--premium", action="store_true", help="Mark the download row as premium-only.")
        parser.add_argument("--timeout", type=int, default=1800, help="Give up after this many seconds.")
        parser.add_argument("--no-watch", action="store_true", help="Create the job and exit immediately.")
        parser.add_argument("--check", action="store_true", help="Only verify credentials and connectivity.")
        parser.add_argument(
            "--attach-id",
            default="",
            help="Attach an already-imported VideoRef (public id) to --post instead of importing.",
        )

    def handle(self, *args, **options):
        if options["check"]:
            return self._check()

        if options["attach_id"]:
            return self._attach_existing(options)

        source_url = (options["source_url"] or "").strip()
        if not source_url:
            raise CommandError("A source URL is required (or use --check).")
        if options["filename_mode"] == "manual" and not options["filename"]:
            raise CommandError("--filename is required with --filename-mode manual.")
        if not client.is_configured():
            raise CommandError(
                "Uploader credentials are missing. Set UPLOADER_API_KEY_ID and "
                "UPLOADER_API_SECRET in the blog's .env and restart the app."
            )

        try:
            created = client.create_import(
                source_url=source_url,
                title=options["title"],
                filename_mode=options["filename_mode"],
                requested_filename=options["filename"],
                created_by=options.get("username") or "manage.py",
            )
        except client.UploaderError as exc:
            if exc.code == "duplicate_import":
                existing = (exc.payload or {}).get("job", {}).get("id", "")
                raise CommandError(
                    f"This source is already being imported (job {existing}). "
                    "Cancel it or wait for it to finish."
                ) from exc
            raise CommandError(f"Could not create the import: {exc.code} - {exc.message}") from exc

        job = created.get("job", {})
        public_id = job.get("id", "")
        self.stdout.write(f"Job created: {public_id} (status {job.get('status')})")

        if options["no_watch"]:
            self._remember(public_id, job)
            self.stdout.write("Not watching (--no-watch). Check later with: manage.py import_video --check")
            return

        final = self._watch(public_id, timeout=options["timeout"])
        if final.get("status") != "completed":
            error = final.get("error") or {}
            ref = self._remember(public_id, final)
            ref.status = VideoRef.Status.FAILED
            ref.save(update_fields=["status", "updated_at"])
            raise CommandError(
                f"Import {public_id} ended as {final.get('status')}: "
                f"{error.get('code', 'unknown')} - {error.get('message', '')}"
            )

        ref = self._remember(public_id, final)
        self._report(ref)

        if options["post"]:
            self._attach(ref, options["post"], options["quality"], options["premium"])

    # ------------------------------------------------------------------
    def _check(self) -> None:
        """Credential/connectivity probe with no side effects."""
        config = client.get_config()
        self.stdout.write(f"Uploader URL: {config.base_url}")
        self.stdout.write(f"Key id      : {config.key_id or '(missing)'}")
        try:
            health = client.health()
            self.stdout.write(f"Health      : {health}")
        except client.UploaderError as exc:
            raise CommandError(f"{exc.code}: {exc.message}") from exc
        try:
            preview = client.preview_filename(
                original_filename="Extraction-3-2026-nkiri.com.mkv", title="Extraction 3"
            )
            self.stdout.write(f"Signed call : OK (preview auto = {preview.get('auto')})")
        except client.UploaderError as exc:
            raise CommandError(
                f"Signed call failed: {exc.code} - {exc.message}. "
                "Check the key id/secret pair on both sides."
            ) from exc

    def _watch(self, public_id: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last = ""
        while time.monotonic() < deadline:
            try:
                payload = client.import_status(public_id)
            except client.UploaderError as exc:
                self.stdout.write(f"  status check failed ({exc.code}); retrying")
                time.sleep(POLL_SECONDS)
                continue
            job = payload.get("job", {})
            state = job.get("status", "")
            if state != last:
                self.stdout.write(f"  {state} {job.get('progress_percent', 0)}%")
                last = state
            if state in {"completed", "failed", "cancelled"}:
                return job
            time.sleep(POLL_SECONDS)
        raise CommandError(f"Timed out after {timeout}s waiting for {public_id}.")

    def _remember(self, public_id: str, job: dict[str, Any]) -> VideoRef:
        video = job.get("video") or {}
        defaults = {
            "filename": job.get("filename") or video.get("filename") or "",
            "size_bytes": video.get("size_bytes") or job.get("total_bytes") or 0,
            "sha256": video.get("sha256") or "",
            "download_page_url": video.get("download_page_url") or "",
            "status": job.get("status") or VideoRef.Status.PENDING,
        }
        ref, _ = VideoRef.objects.update_or_create(public_id=public_id, defaults=defaults)
        return ref

    def _report(self, ref: VideoRef) -> None:
        size_mb = ref.size_bytes / (1024 * 1024) if ref.size_bytes else 0
        self.stdout.write(self.style.SUCCESS("Import complete."))
        self.stdout.write(f"  filename : {ref.filename}")
        self.stdout.write(f"  size     : {size_mb:.1f} MB")
        self.stdout.write(f"  page     : {ref.download_page_url}")

    def _attach_existing(self, options) -> None:
        """Attach a video that was imported earlier to a post."""
        public_id = (options["attach_id"] or "").strip()
        try:
            ref = VideoRef.objects.get(public_id=public_id)
        except VideoRef.DoesNotExist as exc:
            raise CommandError(f"No stored video with id {public_id}.") from exc
        if not ref.is_ready:
            raise CommandError(
                f"Video {public_id} is not ready (status {ref.status}); import it to completion first."
            )
        if not options["post"]:
            raise CommandError("--post is required with --attach-id.")
        self._report(ref)
        self._attach(ref, options["post"], options["quality"], options["premium"])

    def _attach(self, ref: VideoRef, post_id: int, quality: str, premium: bool) -> None:
        from core.models import DownloadQuality, Post

        try:
            post = Post.objects.get(pk=post_id)
        except Post.DoesNotExist as exc:
            raise CommandError(f"Post {post_id} does not exist.") from exc

        valid = {choice for choice, _label in DownloadQuality.QUALITY_CHOICES}
        if quality not in valid:
            raise CommandError(f"--quality must be one of: {', '.join(sorted(valid))}")

        ref.post = post
        ref.save(update_fields=["post", "updated_at"])

        try:
            row = DownloadQuality.objects.create(
                post=post,
                quality=quality,
                download_url=ref.download_page_url,
                is_premium=premium,
            )
        except IntegrityError as exc:  # pragma: no cover - defensive
            raise CommandError(f"Could not create the download row: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Attached to post {post.pk} as a {quality} download row "
                f"({'premium' if premium else 'free'}), id {row.pk}."
            )
        )
