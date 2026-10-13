from django.db import models
from django.utils import timezone


class PageVisit(models.Model):
    """
    Lightweight first-party traffic analytics record.
    Tracks public pageviews without invasive cookies or PII.
    """
    DEVICE_CHOICES = [
        ('desktop', 'Desktop'),
        ('mobile', 'Mobile'),
        ('tablet', 'Tablet'),
        ('bot', 'Bot/Crawler'),
        ('other', 'Other'),
    ]

    path = models.CharField(max_length=500, db_index=True)
    method = models.CharField(max_length=10, default='GET')
    status_code = models.PositiveSmallIntegerField(default=200)
    
    # Anonymized visitor identifier (salted daily/session hash) for unique visitor counts
    visitor_id = models.CharField(max_length=64, db_index=True)
    session_key = models.CharField(max_length=40, blank=True, null=True, db_index=True)
    
    # Traffic attribution
    referrer = models.CharField(max_length=500, blank=True, default='')
    referrer_domain = models.CharField(max_length=150, blank=True, default='', db_index=True)
    
    # Device / environment breakdown
    device_type = models.CharField(max_length=20, choices=DEVICE_CHOICES, default='desktop', db_index=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')
    
    # User status
    is_authenticated = models.BooleanField(default=False)
    is_staff_user = models.BooleanField(default=False)
    
    # Timestamp with index for fast date range filtering
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = 'Page Visit'
        verbose_name_plural = 'Page Visits'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'path']),
            models.Index(fields=['timestamp', 'referrer_domain']),
            models.Index(fields=['timestamp', 'device_type']),
        ]

    def __str__(self):
        return f"{self.path} ({self.timestamp.strftime('%Y-%m-%d %H:%M')})"
