import os
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand
from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.contrib.auth.models import User
from django.utils import timezone
import requests
from bs4 import BeautifulSoup
from core.models import Post, Category, DownloadQuality, Subtitle

class Command(BaseCommand):
    help = 'Imports WordPress content from XML export'

    def add_arguments(self, parser):
        parser.add_argument('xml_file', type=str, help='Path to WordPress XML export file')
        parser.add_argument(
            '--default-category',
            type=str,
            help='Default category slug if none specified',
            default='uncategorized'
        )
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip posts that already exist (default is to overwrite)',
            default=False
        )

    def handle(self, *args, **options):
        tree = ET.parse(options['xml_file'])
        root = tree.getroot()
        
        # WordPress XML namespaces
        ns = {
            'wp': 'http://wordpress.org/export/1.2/',
            'content': 'http://purl.org/rss/1.0/modules/content/',
            'excerpt': 'http://wordpress.org/export/1.2/excerpt/'
        }
        
        # Get or create author
        author = User.objects.first()
        if not author:
            author = User.objects.create_superuser('admin', 'admin@example.com', 'password')
        
        # Get or create default category
        default_category, _ = Category.objects.get_or_create(
            slug=options['default_category'],
            defaults={'name': options['default_category'].title()}
        )
        
        # First pass: Import categories
        self.stdout.write("Importing categories...")
        categories = {}
        for term in root.findall('.//wp:category', ns):
            name = term.find('wp:cat_name', ns).text
            slug = term.find('wp:category_nicename', ns).text
            
            category, created = Category.objects.get_or_create(
                slug=slug,
                defaults={'name': name}
            )
            categories[slug] = category
            if created:
                self.stdout.write(f"Created category: {name}")
        
        # Second pass: Import posts
        self.stdout.write("\nImporting posts...")
        for item in root.findall('.//item'):
            post_type = item.find('wp:post_type', ns).text
            if post_type != 'post':
                continue
                
            title = item.find('title').text or 'Untitled'
            content = item.find('content:encoded', ns).text or ''
            excerpt = item.find('excerpt:encoded', ns).text or ''
            wp_slug = item.find('wp:post_name', ns).text
            pub_date = item.find('wp:post_date', ns).text
            status = item.find('wp:status', ns).text
            
            # Skip drafts if needed
            if status != 'publish':
                continue
            
            # Check if post exists
            if Post.objects.filter(slug=wp_slug).exists():
                if options['skip_existing']:
                    self.stdout.write(f"Skipped existing: {title}")
                    continue
                else:
                    post = Post.objects.get(slug=wp_slug)
                    self.stdout.write(f"Updating existing: {title}")
            else:
                post = Post(slug=wp_slug)
                self.stdout.write(f"Importing new: {title}")
            
            # Process content
            content = self.clean_content(content)
            
            # Update post fields
            post.title = title
            post.content = content
            post.excerpt = excerpt
            post.author = author
            post.published_date = timezone.datetime.strptime(pub_date, '%Y-%m-%d %H:%M:%S')
            post.is_published = True
            
            # Set category
            post_category = default_category
            post_categories = item.findall('category[@domain="category"]')
            if post_categories:
                first_cat_slug = post_categories[0].attrib.get('nicename')
                post_category = categories.get(first_cat_slug, default_category)
            post.category = post_category
            
            # Handle featured image
            post_thumbnail = item.find('wp:postmeta[wp:meta_key="_thumbnail_id"]/wp:meta_value', ns)
            if post_thumbnail is not None:
                self.set_featured_image(post, post_thumbnail.text, root, ns)
            
            post.save()
            
            # Process download links if they exist in content
            self.process_download_links(post, content)
    
    def clean_content(self, content):
        """Convert WordPress content to CKEditor-friendly HTML"""
        soup = BeautifulSoup(content, 'html.parser')
        
        # Remove WordPress-specific shortcodes
        for element in soup.find_all(class_='wp-block-*'):
            element.decompose()
        
        return str(soup)
    
    def set_featured_image(self, post, thumbnail_id, root, ns):
        """Find and download the featured image"""
        attachment = root.find(f'.//item[wp:post_id="{thumbnail_id}"]', ns)
        if attachment is None:
            return
            
        image_url = attachment.find('wp:attachment_url', ns).text
        if not image_url:
            return
            
        try:
            response = requests.get(image_url, stream=True)
            if response.status_code == 200:
                img_temp = NamedTemporaryFile(delete=True)
                img_temp.write(response.content)
                img_temp.flush()
                
                filename = os.path.basename(image_url)
                post.thumbnail.save(filename, File(img_temp))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to download image {image_url}: {str(e)}"))
    
    def process_download_links(self, post, content):
        """Extract download links from content"""
        soup = BeautifulSoup(content, 'html.parser')
        
        # Example: Look for download links
        for link in soup.find_all('a', string=lambda t: t and 'download' in t.lower()):
            DownloadQuality.objects.get_or_create(
                post=post,
                quality='480p',  # Default, could parse from text
                download_url=link.get('href')
            )
        
        # Example: Look for subtitle links
        for link in soup.find_all('a', string=lambda t: t and 'subtitle' in t.lower()):
            Subtitle.objects.get_or_create(
                post=post,
                language='English',  # Default
                download_url=link.get('href')
            )