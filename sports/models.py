from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django_resized import ResizedImageField


class SportsMatch(models.Model):
    """
    Model representing a live or upcoming sports streaming match event.
    """
    SPORT_CHOICES = [
        ('Football', 'Football / Soccer'),
        ('Basketball', 'Basketball'),
        ('Boxing', 'Boxing / MMA / UFC'),
        ('Tennis', 'Tennis'),
        ('Formula 1', 'Formula 1 / Racing'),
        ('Other', 'Other Sports'),
    ]

    title = models.CharField(
        max_length=250,
        help_text="Match title, e.g. 'Chelsea vs Arsenal' or 'UFC 300: Main Card'"
    )
    slug = models.SlugField(
        max_length=250,
        unique=True,
        help_text="Unique URL slug (auto-generated from title)"
    )
    thumbnail = ResizedImageField(
        size=[450, 300],
        quality=85,
        upload_to='sports_thumbnails/',
        blank=True,
        null=True,
        help_text="Match poster image (Recommended size: 450x300px)"
    )
    description = models.TextField(
        blank=True,
        help_text="Match summary or description"
    )
    embed_code = models.TextField(
        blank=True,
        help_text="HTML / iframe embed code for the live stream (Only rendered for VIP subscribers)"
    )
    sport = models.CharField(
        max_length=100,
        choices=SPORT_CHOICES,
        default='Football',
        help_text="Category of sport"
    )
    team_home = models.CharField(
        max_length=100,
        blank=True,
        help_text="Home team/player name (optional)"
    )
    team_away = models.CharField(
        max_length=100,
        blank=True,
        help_text="Away team/player name (optional)"
    )
    match_date = models.DateField(
        default=timezone.now,
        help_text="Date of the match"
    )
    match_time = models.TimeField(
        blank=True,
        null=True,
        help_text="Kick-off / Start time (optional)"
    )
    is_published = models.BooleanField(
        default=True,
        help_text="Check to publish on the website"
    )
    is_featured = models.BooleanField(
        default=False,
        help_text="Check to highlight on top of sports listings"
    )
    views = models.PositiveIntegerField(
        default=0,
        help_text="Total page views"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-match_date', '-created_at']
        verbose_name = "Sports Match"
        verbose_name_plural = "Sports Matches"

    def __str__(self):
        return f"{self.title} ({self.sport} - {self.match_date})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('sports_detail', kwargs={'slug': self.slug})

    @property
    def formatted_datetime(self):
        if self.match_time:
            return f"{self.match_date.strftime('%B %d, %Y')} at {self.match_time.strftime('%I:%M %p')}"
        return self.match_date.strftime('%B %d, %Y')
