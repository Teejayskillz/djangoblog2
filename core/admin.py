# core/admin.py

from django.contrib import admin
from django.utils.html import format_html
from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages, admin
import os
import csv
from django_ckeditor_5.widgets import CKEditor5Widget
from .models import Post, Subscriber,Category, Comment, HomepageSection, DownloadQuality, Subtitle, Page, Media 

# WordPress Import Form and Functionality
class ImportForm(forms.Form):
    xml_file = forms.FileField(label="WordPress Export XML")
    default_category = forms.ModelChoiceField(
        queryset=Category.objects.all(),
        required=True,
        label="Default Category for Posts"
    )
    skip_existing = forms.BooleanField(
        required=False,
        initial=False,
        label="Skip existing posts",
        help_text="Check this to skip posts that already exist (uncheck to update them)"
    )

class PostAdminForm(forms.ModelForm):
    content = forms.CharField(widget=CKEditor5Widget())
    
    class Meta:
        model = Post
        fields = '__all__'
        
class QualityInline(admin.TabularInline):
    model = DownloadQuality
    extra = 1
    fields = ('quality', 'download_url', 'is_premium')

class SubtitleInline(admin.TabularInline):
    model = Subtitle
    extra = 1
    fields = ('language', 'download_url', 'is_auto_generated')

# blog/admin.py
import json
import requests
from django.contrib import admin, messages
from django.conf import settings
from .models import Post

@admin.action(description="Share selected posts via EmailHub")
def share_via_email(modeladmin, request, queryset):
    site_url = getattr(settings, "SITE_URL", "https://nzdworld.com")
    url = "https://mailhub.nzdworld.com/api/receive-post/"

    for post in queryset:
        # Build thumbnail URL if available
        thumbnail_url = f"{site_url}{post.thumbnail.url}" if post.thumbnail else ""
        read_more_url = f"{site_url}{post.get_absolute_url()}"

        # Prepare data payload
        data = {
            "title": post.title,
            "content": post.excerpt or post.content[:500],
            "thumbnail_url": thumbnail_url,
            "read_more_url": read_more_url,
        }

        try:
            response = requests.post(
                url,
                data=json.dumps(data),
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            resp_data = response.json()
            messages.success(request, f"✅ Sent '{post.title}' to EmailHub: {resp_data.get('message', 'OK')}")
        except Exception as e:
            messages.error(request, f"❌ Failed to send '{post.title}': {e}")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    form = PostAdminForm
    list_display = (
        'title',
        'author',
        'category',
        'is_published',
        'enable_downloads',
        'get_has_downloads',
        'get_thumbnail_preview',
        'get_total_downloads',
        'is_password_protected',
    )
    actions = [share_via_email]
    list_filter = (
        'category',
        'tags',
        'is_published',
        'enable_downloads'
    )
    list_editable = ('is_published', 'enable_downloads')
    prepopulated_fields = {'slug': ('title',)}
    
    # MODIFIED: Added 'seo_title' to search_fields
    search_fields = ('title', 'content', 'excerpt', 'seo_title') 
    
    readonly_fields = (
        'get_thumbnail_preview',
        'get_total_downloads',
        'get_downloads_preview'
    )
    inlines = [QualityInline, SubtitleInline]
    change_list_template = 'admin/blog/post/change_list.html'

    fieldsets = (
        ('Content', {
            'fields': (
                'title',
                'slug',
                'seo_title', # MODIFIED: Added 'seo_title' here
                'content',
                'excerpt',
                'thumbnail',
                'author',
                'category',
                'tags',
                'is_published',
                'shared_via_email',
                'is_password_protected',
                'password'
            )
        }),
        ('Download Settings', {
            'fields': (
                'enable_downloads',
                'download_section_title',
                'get_downloads_preview'
            ),
            'classes': ('collapse',)
        }),
        ('Statistics', {
            'fields': ('get_total_downloads',),
            'classes': ('collapse',)
        }),
    )

    # WordPress Import Methods
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('import-wordpress/', self.admin_site.admin_view(self.import_wordpress), name='import_wordpress'),
        ]
        return custom_urls + urls
    
    def import_wordpress(self, request):
        if request.method == 'POST':
            form = ImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    # Save the uploaded file temporarily
                    xml_file = request.FILES['xml_file']
                    temp_path = os.path.join('/tmp', xml_file.name)
                    with open(temp_path, 'wb+') as destination:
                        for chunk in xml_file.chunks():
                            destination.write(chunk)
                    
                    # Run the import command with correct arguments
                    from django.core.management import call_command
                    call_command(
                        'import_wordpress', 
                        temp_path,
                        default_category=form.cleaned_data['default_category'].slug,
                        skip_existing=form.cleaned_data['skip_existing']
                    )
                    
                    messages.success(request, 'WordPress content imported successfully!')
                    return redirect('..')
                except Exception as e:
                    messages.error(request, f'Error during import: {str(e)}')
        else:
            form = ImportForm()
        
        context = {
            'form': form,
            'opts': self.model._meta,
            **self.admin_site.each_context(request),
        }
        return render(request, 'admin/wordpress_import.html', context)

    # Existing Admin Methods
    def get_thumbnail_preview(self, obj):
        if obj.thumbnail:
            return format_html(
                '<img src="{}" style="max-height: 100px; max-width: 100px; object-fit: cover;" />',
                obj.thumbnail.url
            )
        return "No thumbnail"
    get_thumbnail_preview.short_description = 'Thumbnail Preview'

    def get_total_downloads(self, obj):
        qualities = sum(q.download_count for q in obj.qualities.all())
        subtitles = sum(s.download_count for s in obj.subtitles.all())
        return format_html(
            "▶️ <b>Video:</b> {}<br>"
            "📝 <b>Subtitles:</b> {}",
            qualities,
            subtitles
        )
    get_total_downloads.short_description = '📊 Downloads'

    def get_downloads_preview(self, obj):
        if obj.enable_downloads:
            qualities = obj.qualities.count()
            subtitles = obj.subtitles.count()
            return format_html(
                "<div style='background:#f8f9fa;padding:10px;border-radius:5px;'>"
                "<b>Will display:</b> {}<br>"
                "<b>Qualities:</b> {}<br>"
                "<b>Subtitles:</b> {}"
                "</div>",
                obj.download_section_title,
                qualities or "None added",
                subtitles or "None added"
            )
        return "❌ Download section will be hidden"
    get_downloads_preview.short_description = 'Live Preview'

    def get_has_downloads(self, obj):
        return obj.enable_downloads
    get_has_downloads.boolean = True
    get_has_downloads.short_description = 'Downloads'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # When the admin checks the "shared_via_email" box
        if obj.shared_via_email:
            url = "https://mailhub.nzdworld.com/api/receive-post/"
            data = {
                "title": obj.title,
                "content": obj.excerpt or obj.content[:500],
            }

            try:
                response = requests.post(
                    url,
                    data=json.dumps(data),
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                resp_data = response.json()
                messages.success(request, f"✅ Sent '{obj.title}' to EmailHub: {resp_data.get('message', 'OK')}")

                # Auto-uncheck the box after sharing
                obj.shared_via_email = False
                obj.save(update_fields=["shared_via_email"])

            except Exception as e:
                messages.error(request, f"❌ Failed to send '{obj.title}': {e}")

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('name', 'post', 'created_at', 'is_approved')
    list_filter = ('is_approved', 'created_at')
    list_editable = ('is_approved',)
    search_fields = ('name', 'email', 'comment')
    actions = ['approve_comments']

    def approve_comments(self, request, queryset):
        queryset.update(is_approved=True)
    approve_comments.short_description = "Approve selected comments"

@admin.register(HomepageSection)
class HomepageSectionAdmin(admin.ModelAdmin):
    filter_horizontal = ('categories',)
    list_display = ('title', 'enabled', 'display_order')
    list_editable = ('enabled', 'display_order')
    
@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'is_published')
    prepopulated_fields = {'slug': ('title',)}
    search_fields = ('title', 'content')
    list_filter = ('is_published',)
    fieldsets = (
        (None, {
            'fields': ('title', 'slug', 'content', 'is_published')
        }),
    )
    
@admin.register(Media)
class MediaAdmin(admin.ModelAdmin):
    list_display = ('title', 'uploaded_at', 'thumbnail_preview', 'file_link_display')
    search_fields = ('title', 'file')
    list_filter = ('uploaded_at',)
    readonly_fields = ('uploaded_at', 'thumbnail_preview_detail', 'file_link_field')

    def thumbnail_preview(self, obj):
        """Displays the thumbnail in the list view, whether manual or auto-generated."""
        if obj.thumbnail: # This is the key: if the field HAS a file, display it
            return format_html('<img src="{}" style="max-height: 60px; max-width: 60px; object-fit: contain; border: 1px solid #eee;" />', obj.thumbnail.url)
        return format_html('<i class="fas fa-file fa-2x text-muted" style="line-height: 60px;"></i>')
    thumbnail_preview.short_description = 'Thumbnail'

    def thumbnail_preview_detail(self, obj):
        """Displays the thumbnail on the detail page."""
        if obj.thumbnail: # Again, checks if the field has a file
            file_name_lower = obj.thumbnail.name.lower() # Use thumbnail.name for extension check
            if file_name_lower.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                return format_html('<img src="{}" class="img-fluid border" style="max-width: 200px; height: auto;" />', obj.thumbnail.url)
            # You might want to handle video/audio in the thumbnail_preview_detail if your 'thumbnails' folder
            # could contain actual video/audio files (less common for thumbnails).
            # For typical image thumbnails of videos/audio, the above `<img>` tag is sufficient.
            else:
                 return format_html('<p class="text-muted">File is not a common image format, but linked: <a href="{}" target="_blank">{}</a></p>', obj.thumbnail.url, obj.thumbnail.name)
        return '<p class="text-muted">No thumbnail available.</p>'
    thumbnail_preview_detail.short_description = 'Thumbnail Preview'

    def file_link_display(self, obj):
        """Displays a direct link to the original file in the list view."""
        if obj.file:
            return format_html('<a href="{}" target="_blank">{}</a>', obj.file.url, obj.file.name)
        return "No file"
    file_link_display.short_description = 'Original File'

    def file_link_field(self, obj):
        """Displays the direct link to the original file on the detail page."""
        if obj.file:
            return format_html('<p><strong>Original File URL:</strong> <a href="{}" target="_blank">{}</a></p>', obj.file.url, obj.file.url)
        return "No original file."
    file_link_field.short_description = 'Original File URL'


    # Ensure these custom fields appear in the admin change form
    fieldsets = (
        (None, {
            'fields': ('title', 'file', 'thumbnail_preview_detail', 'file_link_field', 'uploaded_at')
        }),
    )



@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'created_at')
    search_fields = ['email']

    # Your custom methods (get_urls, import_view, etc.) go here