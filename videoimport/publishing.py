"""
Publish orchestration on top of the **existing** uploader API.

Nothing here replaces the transfer mechanism: each row is still sent to the same
``POST /api/v1/imports/`` endpoint with the same HMAC auth and the same payload,
only with the row's filename choice attached. What is new is the bookkeeping:

* the post is forced to draft while uploads run;
* one :class:`UploadBatch` records the run, with a version so an old run can
  never publish a newer edit;
* every row's state is mirrored from the uploader's own job status;
* the post is published only when *all* queued rows are confirmed completed.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import DownloadQuality
from videoimport import client
from videoimport.models import UploadBatch

# Uploader job status -> row status shown in the admin.
STATUS_MAP = {
    "queued": DownloadQuality.ProcessingStatus.QUEUED,
    "downloading": DownloadQuality.ProcessingStatus.DOWNLOADING,
    "transferring": DownloadQuality.ProcessingStatus.UPLOADING,
    "processing": DownloadQuality.ProcessingStatus.UPLOADING,
    "completed": DownloadQuality.ProcessingStatus.COMPLETED,
    "failed": DownloadQuality.ProcessingStatus.FAILED,
    "cancelled": DownloadQuality.ProcessingStatus.FAILED,
}


def uploader_origin() -> str:
    return str(getattr(settings, "UPLOADER_API_URL", "")).rstrip("/")


def is_processed_url(url: str) -> bool:
    """True when the URL already points at our uploader's download page."""
    origin = uploader_origin()
    return bool(url and origin and url.startswith(origin + "/d/"))


def row_source_url(row: DownloadQuality) -> str:
    """The direct URL to fetch: the stored source, else the pasted link."""
    if row.source_url:
        return row.source_url
    if row.download_url and not is_processed_url(row.download_url):
        return row.download_url
    return ""


def row_title(post, row: DownloadQuality) -> str:
    """
    Title handed to the uploader for automatic naming.

    The quality label is appended so a series/film produces
    ``The-Black-Book-2026-720p`` rather than 40 identical names.
    """
    base = (post.title or "").strip()
    label = row.quality_short_label
    if label and label.lower() not in base.lower():
        return f"{base} {label}".strip()
    return base or label


def next_version(post) -> int:
    current = (
        UploadBatch.objects.filter(post=post).order_by("-version").values_list("version", flat=True).first()
    )
    return (current or 0) + 1


def latest_batch(post) -> UploadBatch | None:
    return UploadBatch.objects.filter(post=post).order_by("-version").first()


def is_latest(batch: UploadBatch) -> bool:
    newest = latest_batch(batch.post)
    return newest is not None and newest.pk == batch.pk


def queue_publish(post, *, rows=None) -> UploadBatch:
    """
    Save the post as a draft and queue every configured download row.

    Only rows with a usable source URL are queued. Rows already completed are
    skipped, so re-publishing after an edit does not re-upload finished files.
    """
    rows = list(rows if rows is not None else post.qualities.all())
    candidates = [row for row in rows if row_source_url(row) and not row.is_processed]

    version = next_version(post)
    batch = UploadBatch.objects.create(post=post, version=version)

    if not candidates:
        # Never silently publish a post with nothing to upload.
        batch.status = UploadBatch.Status.FAILED
        batch.error = "No download rows with a URL to upload."
        batch.save(update_fields=["status", "error", "updated_at"])
        return batch

    post.is_published = False
    post.save(update_fields=["is_published"])

    failures = 0
    for row in candidates:
        source = row_source_url(row)
        if not row.source_url:
            row.source_url = source
        row.processing_status = DownloadQuality.ProcessingStatus.QUEUED
        row.progress_percent = 0
        row.processing_error = ""
        row.save(
            update_fields=[
                "source_url",
                "processing_status",
                "progress_percent",
                "processing_error",
                "download_url",
            ]
        )

        try:
            payload = client.create_import(
                source_url=source,
                title=row_title(post, row),
                filename_mode=row.filename_mode,
                requested_filename=row.requested_filename,
                created_by=getattr(post.author, "username", "") or "control-center",
            )
            job = payload.get("job") or {}
            row.job_public_id = str(job.get("id") or "")[:40]
            row.processing_status = STATUS_MAP.get(
                job.get("status", ""), DownloadQuality.ProcessingStatus.QUEUED
            )
        except client.UploaderError as exc:
            if exc.code == "duplicate_import" and (exc.payload or {}).get("job"):
                # An identical source is already in flight: adopt that job.
                row.job_public_id = str(exc.payload["job"].get("id") or "")[:40]
                row.processing_status = DownloadQuality.ProcessingStatus.QUEUED
            else:
                failures += 1
                row.processing_status = DownloadQuality.ProcessingStatus.FAILED
                row.processing_error = f"{exc.code}: {exc.message}"[:2000]
        row.save(
            update_fields=["job_public_id", "processing_status", "processing_error", "source_url", "download_url"]
        )

    if failures:
        batch.status = UploadBatch.Status.FAILED
        batch.error = f"{failures} row(s) could not be queued."
        batch.save(update_fields=["status", "error", "updated_at"])
    else:
        batch.status = UploadBatch.Status.PROCESSING
        batch.save(update_fields=["status", "updated_at"])
    return batch


def sync_batch(batch: UploadBatch) -> UploadBatch:
    """Refresh one batch from the uploader and publish when everything is done."""
    if batch.status in (UploadBatch.Status.COMPLETED, UploadBatch.Status.FAILED):
        return batch

    rows = list(batch.post.qualities.all())
    pending = False
    failed = False

    for row in rows:
        if row.is_processed:
            continue
        if not row.job_public_id:
            # Queued locally but never accepted by the uploader: surface it.
            row.processing_status = DownloadQuality.ProcessingStatus.FAILED
            row.processing_error = "Not queued with the uploader. Use Retry."
            row.save(update_fields=["processing_status", "processing_error"])
            failed = True
            continue
        try:
            payload = client.import_status(row.job_public_id)
        except client.UploaderError as exc:
            if exc.code in {"not_found"}:
                row.processing_status = DownloadQuality.ProcessingStatus.FAILED
                row.processing_error = "The uploader has no record of this job."
                row.save(update_fields=["processing_status", "processing_error"])
                failed = True
            else:
                pending = True
            continue

        job = payload.get("job") or {}
        state = str(job.get("status") or "")
        row.processing_status = STATUS_MAP.get(state, row.processing_status)
        row.progress_percent = int(job.get("progress_percent") or 0)

        if state == "completed":
            video = job.get("video") or {}
            if video.get("download_page_url"):
                row.download_url = video["download_page_url"][:200]
            row.progress_percent = 100
            row.processed_at = timezone.now()
            row.processing_error = ""
        elif state in {"failed", "cancelled"}:
            error = job.get("error") or {}
            row.processing_error = f"{error.get('code', 'failed')}: {error.get('message', '')}"[:2000]
            failed = True
        else:
            pending = True
        row.save(
            update_fields=[
                "processing_status",
                "progress_percent",
                "processing_error",
                "download_url",
                "processed_at",
            ]
        )

    batch = _finalise(batch, rows=rows, pending=pending, failed=failed)
    return batch


def _finalise(batch: UploadBatch, *, rows, pending: bool, failed: bool) -> UploadBatch:
    if failed:
        batch.status = UploadBatch.Status.FAILED
        batch.error = "One or more rows failed. The post stays a draft."
    elif pending:
        batch.status = UploadBatch.Status.PROCESSING
        batch.error = ""
    else:
        batch.status = UploadBatch.Status.COMPLETED
        batch.error = ""
        batch.published_at = timezone.now()

    with transaction.atomic():
        batch.save(update_fields=["status", "error", "published_at", "updated_at"])
        if batch.status == UploadBatch.Status.COMPLETED and is_latest(batch):
            # Only the newest run may publish, and only a still-draft post.
            post = batch.post
            if not post.is_published:
                post.is_published = True
                post.save(update_fields=["is_published"])
    return batch


def sync_all() -> dict:
    """Used by the cron command: refresh every unfinished batch."""
    active = UploadBatch.objects.filter(
        status__in=[UploadBatch.Status.QUEUED, UploadBatch.Status.PROCESSING]
    ).select_related("post")
    completed = failed = 0
    for batch in active:
        batch = sync_batch(batch)
        if batch.status == UploadBatch.Status.COMPLETED:
            completed += 1
        elif batch.status == UploadBatch.Status.FAILED:
            failed += 1
    return {"checked": active.count(), "completed": completed, "failed": failed}


def retry_row(row: DownloadQuality) -> DownloadQuality:
    """Re-run a failed row through the existing uploader, reusing the same job."""
    if row.job_public_id:
        try:
            client.retry_import(row.job_public_id)
            row.processing_status = DownloadQuality.ProcessingStatus.QUEUED
            row.processing_error = ""
            row.progress_percent = 0
            row.save(update_fields=["processing_status", "processing_error", "progress_percent"])
            if row.post:
                latest = latest_batch(row.post)
                if latest and latest.status == UploadBatch.Status.FAILED:
                    latest.status = UploadBatch.Status.PROCESSING
                    latest.error = ""
                    latest.save(update_fields=["status", "error", "updated_at"])
            return row
        except client.UploaderError:
            pass  # job gone: fall through and queue a fresh one

    source = row_source_url(row)
    if not source:
        row.processing_status = DownloadQuality.ProcessingStatus.FAILED
        row.processing_error = "No source URL to retry."
        row.save(update_fields=["processing_status", "processing_error"])
        return row

    payload = client.create_import(
        source_url=source,
        title=row_title(row.post, row),
        filename_mode=row.filename_mode,
        requested_filename=row.requested_filename,
        created_by="retry",
    )
    job = payload.get("job") or {}
    row.job_public_id = str(job.get("id") or "")[:40]
    row.processing_status = DownloadQuality.ProcessingStatus.QUEUED
    row.processing_error = ""
    row.progress_percent = 0
    row.save(update_fields=["job_public_id", "processing_status", "processing_error", "progress_percent"])
    return row


def sync_for_post(post, *, throttle_seconds: int = 5):
    """
    Refresh a post's active batch on demand.

    Called from the admin UI (status endpoint and post list) so progress moves
    even when the cron command is not installed. Throttled per post so a page
    refresh cannot hammer the uploader API.
    """
    from django.core.cache import cache

    batch = latest_batch(post)
    if batch is None or batch.status in (UploadBatch.Status.COMPLETED, UploadBatch.Status.FAILED):
        return batch

    key = f"nzd:batch-sync:{post.pk}"
    if throttle_seconds and cache.get(key):
        return batch
    cache.set(key, 1, throttle_seconds)
    return sync_batch(batch)


def sync_page(objects, *, limit: int = 5, throttle_seconds: int = 30) -> int:
    """
    Refresh the newest active batch for each post on a list page.

    Bounded (``limit``) and throttled, because this runs while rendering the
    post list; the cron command remains the reliable background path.
    """
    post_ids = [getattr(obj, "pk", None) for obj in objects]
    post_ids = [pk for pk in post_ids if pk]
    if not post_ids:
        return 0

    active = (
        UploadBatch.objects.filter(
            post_id__in=post_ids,
            status__in=[UploadBatch.Status.QUEUED, UploadBatch.Status.PROCESSING],
        )
        .select_related("post")
        .order_by("-version")
    )

    seen: set[int] = set()
    synced = 0
    for batch in active:
        if batch.post_id in seen:
            continue
        seen.add(batch.post_id)
        sync_for_post(batch.post, throttle_seconds=throttle_seconds)
        synced += 1
        if synced >= limit:
            break
    return synced