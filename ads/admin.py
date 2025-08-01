# ads/admin.py

from django.contrib import admin
from .models import Ad

@admin.register(Ad)
class AdAdmin(admin.ModelAdmin):
    """
    Customizes the display and functionality of the Ad model in the Django admin.
    """
    list_display = ('name', 'slug', 'is_active', 'created_at', 'updated_at') # Fields to display in the list view
    list_filter = ('is_active', 'created_at', 'updated_at') # Filters on the right sidebar
    search_fields = ('name', 'ad_content', 'slug') # Fields to search by
    prepopulated_fields = {'slug': ('name',)} # Automatically populate slug from name
    readonly_fields = ('created_at', 'updated_at') # Make these fields read-only in the admin form
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'ad_content', 'is_active')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',) # Collapse this section by default
        }),
    )

