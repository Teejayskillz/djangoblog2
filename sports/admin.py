from django.contrib import admin
from django.utils.html import format_html
from .models import SportsMatch


@admin.register(SportsMatch)
class SportsMatchAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'sport',
        'match_date',
        'match_time',
        'is_published',
        'is_featured',
        'views',
        'get_thumbnail_preview',
        'created_at'
    )
    list_filter = ('sport', 'is_published', 'is_featured', 'match_date')
    list_editable = ('is_published', 'is_featured')
    search_fields = ('title', 'description', 'team_home', 'team_away', 'sport')
    prepopulated_fields = {'slug': ('title',)}
    readonly_fields = ('get_thumbnail_preview', 'views', 'created_at', 'updated_at')

    fieldsets = (
        ('Match Basic Info', {
            'fields': (
                'title',
                'slug',
                'sport',
                'team_home',
                'team_away',
                'thumbnail',
                'get_thumbnail_preview',
                'description'
            )
        }),
        ('Schedule', {
            'fields': (
                'match_date',
                'match_time'
            )
        }),
        ('Live Stream Player Embed', {
            'fields': (
                'embed_code',
            ),
            'description': 'Paste HTML or iframe embed code here. This is securely restricted on the backend to active VIP subscribers only.'
        }),
        ('Publishing & Visibility', {
            'fields': (
                'is_published',
                'is_featured'
            )
        }),
        ('Statistics & Metadata', {
            'fields': ('views', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )

    def get_thumbnail_preview(self, obj):
        if obj.thumbnail:
            return format_html(
                '<img src="{}" style="max-height: 70px; max-width: 100px; object-fit: cover; border-radius: 6px; border: 1px solid #3F516C;" />',
                obj.thumbnail.url
            )
        return format_html('<span style="color: #9CA3AF;">No Poster</span>')
    get_thumbnail_preview.short_description = 'Poster Preview'
