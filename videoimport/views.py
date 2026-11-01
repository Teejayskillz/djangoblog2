"""
Control-center proxy endpoints for the video import panel (brief section 8).

The browser never sees the uploader's API secret: it calls these staff-only
views, which sign the request server-side and forward it. Every response is
JSON so the panel can poll without reloading the post form.
"""

from __future__ import annotations

import json

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from videoimport import client
from videoimport.models import VideoRef

QUALITY_CHOICES = None  # resolved lazily to avoid importing models at startup


def _error(code: str, message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": code, "message": message}, status=status)


def _body(request) -> dict:
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _guard(exc: client.UploaderError) -> JsonResponse:
    status = 409 if exc.code == "duplicate_import" else (502 if (exc.status or 0) >= 500 else 400)
    return JsonResponse(
        {"error": exc.code, "message": exc.message, "job": (exc.payload or {}).get("job")},
        status=status,
    )


@staff_member_required
@require_POST
def api_preview(request):
    payload = _body(request)
    original = str(payload.get("original_filename") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not original:
        return _error("missing_original_filename", "A source filename is required.")
    try:
        return JsonResponse(client.preview_filename(
            original_filename=original, title=title, manual_name=str(payload.get("manual_name") or "")
        ))
    except client.UploaderError as exc:
        return _guard(exc)


@staff_member_required
@require_POST
def api_create(request):
    payload = _body(request)
    source_url = str(payload.get("source_url") or "").strip()
    if not source_url:
        return _error("missing_source_url", "Paste a direct video URL.")
    try:
        data = client.create_import(
            source_url=source_url,
            title=str(payload.get("title") or ""),
            filename_mode=str(payload.get("filename_mode") or "auto"),
            requested_filename=str(payload.get("requested_filename") or ""),
            created_by=getattr(request.user, "username", ""),
        )
    except client.UploaderError as exc:
        return _guard(exc)

    job = data.get("job") or {}
    if job.get("id"):
        VideoRef.objects.update_or_create(
            public_id=job["id"],
            defaults={"status": job.get("status") or VideoRef.Status.QUEUED},
        )
    return JsonResponse({"job": job}, status=201)


@staff_member_required
@require_GET
def api_status(request, public_id: str):
    try:
        data = client.import_status(public_id)
    except client.UploaderError as exc:
        return _guard(exc)

    job = data.get("job") or {}
    video = job.get("video") or {}
    VideoRef.objects.update_or_create(
        public_id=public_id,
        defaults={
            "status": job.get("status") or VideoRef.Status.PENDING,
            "filename": job.get("filename") or video.get("filename") or "",
            "size_bytes": video.get("size_bytes") or 0,
            "sha256": video.get("sha256") or "",
            "download_page_url": video.get("download_page_url") or "",
        },
    )
    return JsonResponse({"job": job})


@staff_member_required
@require_POST
def api_cancel(request, public_id: str):
    try:
        return JsonResponse(client.cancel_import(public_id))
    except client.UploaderError as exc:
        return _guard(exc)


@staff_member_required
@require_POST
def api_retry(request, public_id: str):
    try:
        return JsonResponse(client.retry_import(public_id))
    except client.UploaderError as exc:
        return _guard(exc)


@staff_member_required
@require_POST
def api_attach(request):
    """Create the DownloadQuality row that the post template renders."""
    from core.models import DownloadQuality, Post

    payload = _body(request)
    public_id = str(payload.get("public_id") or "").strip()
    try:
        post_id = int(payload.get("post_id") or 0)
    except (TypeError, ValueError):
        return _error("bad_post", "Invalid post id.")

    quality = str(payload.get("quality") or "1080p")
    premium_flag = bool(payload.get("premium"))

    valid = {choice for choice, _label in DownloadQuality.QUALITY_CHOICES}
    if quality not in valid:
        return _error("bad_quality", "Unknown quality option.")

    ref = VideoRef.objects.filter(public_id=public_id).first()
    if ref is None or not ref.download_page_url:
        return _error("not_ready", "That video is not finished importing yet.")
    post = Post.objects.filter(pk=post_id).first()
    if post is None:
        return _error("bad_post", "Save the post first, then attach the video.")

    row = DownloadQuality.objects.create(
        post=post, quality=quality, download_url=ref.download_page_url, is_premium=premium_flag
    )
    ref.post = post
    ref.save(update_fields=["post", "updated_at"])
    return JsonResponse(
        {
            "ok": True,
            "download_id": row.pk,
            "url": row.download_url,
            "quality": row.quality,
            "premium": row.is_premium,
        },
        status=201,
    )
