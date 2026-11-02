"""
Thin reference to a video stored by the uploader service.

The blog deliberately does **not** duplicate the video catalogue: this table only
remembers which uploader video a post points at, plus the handful of fields the
admin UI and templates need. The uploader's database remains authoritative for
the file, its metadata and its download analytics.
"""

from __future__ import annotations

from django.db import models


class VideoRef(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        DOWNLOADING = "downloading", "Downloading"
        TRANSFERRING = "transferring", "Transferring"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    public_id = models.CharField(
        max_length=32,
        unique=True,
        help_text="The uploader's opaque video/job identifier.",
    )
    post = models.ForeignKey(
        "core.Post",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="video_refs",
        help_text="Post this video was imported for. Null if the post was deleted.",
    )
    filename = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    download_page_url = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Video reference"
        verbose_name_plural = "Video references"

    def __str__(self) -> str:
        label = self.filename or self.public_id[:12]
        return f"{label} ({self.status})"

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.COMPLETED and bool(self.download_page_url)


class UploadBatch(models.Model):
    """
    One "Publish & queue uploads" run for a post.

    The post stays a draft until every row in the newest batch reports success
    from the storage service. ``version`` makes an older run unable to publish a
    newer edit: only the highest version for a post may flip ``is_published``.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    post = models.ForeignKey(
        "core.Post", on_delete=models.CASCADE, related_name="upload_batches"
    )
    version = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-version"]
        unique_together = [("post", "version")]
        verbose_name = "Upload batch"
        verbose_name_plural = "Upload batches"

    def __str__(self) -> str:
        return f"post {self.post_id} v{self.version} ({self.status})"