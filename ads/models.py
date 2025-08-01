# ads/models.py

from django.db import models
from django.utils.text import slugify

class Ad(models.Model):
    """
    Model to store advertisement content.
    Each ad has a name, a unique slug for easy referencing in templates,
    the actual HTML/JavaScript content of the ad, and an active status.
    """
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="A human-readable name for the ad (e.g., 'Homepage Banner', 'Sidebar Ad')."
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        blank=True, # Allow blank on creation, will be auto-generated if not provided
        help_text="A unique identifier for the ad, used to reference it in templates (e.g., 'homepage-banner')."
    )
    ad_content = models.TextField(
        help_text="The full HTML or JavaScript code for the advertisement."
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Check to display this ad on the website. Uncheck to temporarily hide it."
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="The date and time when the ad was created."
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        help_text="The date and time when the ad was last updated."
    )

    class Meta:
        verbose_name = "Advertisement"
        verbose_name_plural = "Advertisements"
        ordering = ['name'] # Order ads by name in the admin

    def __str__(self):
        """
        Returns a string representation of the Ad instance.
        """
        return self.name

    def save(self, *args, **kwargs):
        """
        Overrides the save method to automatically generate a slug if one is not provided.
        Ensures the slug is unique.
        """
        if not self.slug:
            self.slug = slugify(self.name)
        # Ensure slug uniqueness on update as well
        original_slug = self.slug
        counter = 1
        while Ad.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
            self.slug = f"{original_slug}-{counter}"
            counter += 1
        super().save(*args, **kwargs)

