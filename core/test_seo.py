"""
Technical/on-page SEO tests for post detail pages and site-wide metadata.
"""

import io
import json
import re

from django.test import TestCase, override_settings
from django.urls import reverse

import core.testing  # noqa: F401  (applies the Python 3.14 test-client patch)
from core.models import Comment
from core.testing import make_comment, make_post, make_user

SITE = 'https://nzdworld.com'
JSON_LD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)
TITLE_RE = re.compile(r'<title>(.*?)</title>', re.DOTALL)


def page_title(html):
    """The <title> text with whitespace normalised."""
    return re.sub(r'\s+', ' ', TITLE_RE.search(html).group(1)).strip()


def image_upload(name='poster.jpg'):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (300, 450), 'teal').save(buffer, format='JPEG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')


@override_settings(SITE_URL=SITE)
class PostDetailSeoTests(TestCase):
    def setUp(self):
        self.author = make_user('writer')
        self.post = make_post(
            title='Henry Danger S1 EP20',
            slug='henry-danger-s1-ep20',
            excerpt='Download Henry Danger season 1 episode 20 in HD with subtitles.',
            author=self.author,
        )
        self.url = self.post.get_absolute_url()

    def response_html(self, url=None):
        response = self.client.get(url or self.url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def json_ld_blocks(self, html):
        return [json.loads(match) for match in JSON_LD_RE.findall(html)]

    def test_title_is_unique_and_descriptive(self):
        self.assertEqual(page_title(self.response_html()), 'Henry Danger S1 EP20 - Nzdworld')

    def test_seo_title_overrides_the_displayed_title(self):
        self.post.seo_title = 'Henry Danger S1 EP20 Download HD'
        self.post.save(update_fields=['seo_title'])

        self.assertEqual(
            page_title(self.response_html()),
            'Henry Danger S1 EP20 Download HD - Nzdworld',
        )

    def test_meta_description_is_content_specific_and_not_empty(self):
        html = self.response_html()

        self.assertIn(
            '<meta name="description"\n        content="Download Henry Danger season 1 episode 20 in HD with subtitles."',
            html.replace('\r\n', '\n'),
        )

    def test_meta_descriptions_differ_between_posts(self):
        other = make_post(
            title='Another Movie', slug='another-movie', excerpt='Completely different synopsis for another film.'
        )

        first_desc = self.client.get(self.url).context['meta_description']
        second_desc = self.client.get(other.get_absolute_url()).context['meta_description']

        self.assertNotEqual(first_desc, second_desc)
        self.assertLessEqual(len(first_desc), 160)

    def test_meta_description_falls_back_to_article_content(self):
        self.post.excerpt = ''
        self.post.content = '<p>' + ('Great movie with a very long plot description. ' * 5) + '</p>'
        self.post.save(update_fields=['excerpt', 'content'])

        description = self.client.get(self.url).context['meta_description']

        self.assertTrue(description.startswith('Great movie with a very long plot'))
        self.assertLessEqual(len(description), 160)

    def test_canonical_url_is_absolute_and_query_free(self):
        html = self.response_html(f'{self.url}?comments_page=2&utm_source=test')

        self.assertIn(f'<link rel="canonical" href="{SITE}{self.url}" />', html)

    def test_open_graph_tags_use_canonical_url_and_real_image(self):
        html = self.response_html()

        self.assertIn(f'<meta property="og:url" content="{SITE}{self.url}">', html)
        self.assertIn('<meta property="og:type" content="article">', html)
        self.assertIn('Henry Danger S1 EP20', html)
        self.assertRegex(html, r'<meta property="og:image" content="https://nzdworld\.com/')

    def test_twitter_card_tags_are_present(self):
        html = self.response_html()

        self.assertIn('<meta name="twitter:card" content="summary_large_image">', html)
        self.assertIn(f'<meta name="twitter:url" content="{SITE}{self.url}">', html)

    def test_exactly_one_h1_and_it_is_the_post_title(self):
        html = self.response_html()

        h1s = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)

        self.assertEqual(len(h1s), 1, f'expected one H1, found {len(h1s)}')
        self.assertIn('Henry Danger S1 EP20', h1s[0])

    def test_article_json_ld_is_valid_and_accurate(self):
        blocks = self.json_ld_blocks(self.response_html())
        articles = [block for block in blocks if block.get('@type') == 'BlogPosting']

        self.assertEqual(len(articles), 1, 'expected exactly one BlogPosting node')
        article = articles[0]
        self.assertEqual(article['headline'], 'Henry Danger S1 EP20')
        self.assertEqual(article['url'], f'{SITE}{self.url}')
        self.assertEqual(article['mainEntityOfPage']['@id'], f'{SITE}{self.url}')
        self.assertEqual(article['author']['name'], 'writer')
        self.assertTrue(article['datePublished'].startswith(str(self.post.published_date.year)))
        self.assertEqual(article['articleSection'], 'Movies')
        # Nothing invented.
        self.assertNotIn('aggregateRating', article)
        self.assertNotIn('review', article)
        self.assertNotIn('dateModified', article)

    def test_breadcrumb_json_ld_describes_home_category_post(self):
        blocks = self.json_ld_blocks(self.response_html())
        breadcrumbs = [block for block in blocks if block.get('@type') == 'BreadcrumbList']

        self.assertEqual(len(breadcrumbs), 1)
        items = breadcrumbs[0]['itemListElement']
        self.assertEqual([item['name'] for item in items], ['Home', 'Movies', 'Henry Danger S1 EP20'])
        self.assertEqual([item['position'] for item in items], [1, 2, 3])
        self.assertEqual(items[0]['item'], f'{SITE}/')
        self.assertNotIn('item', items[-1])

    def test_visible_breadcrumbs_are_rendered(self):
        html = self.response_html()

        self.assertIn('aria-label="Breadcrumb"', html)
        self.assertIn('href="/category/movies/"', html)

    def test_site_wide_json_ld_is_present_once(self):
        blocks = self.json_ld_blocks(self.response_html())
        graph_types = [
            node['@type']
            for block in blocks
            for node in block.get('@graph', [])
        ]

        self.assertIn('WebSite', graph_types)
        self.assertIn('Organization', graph_types)

    def test_json_ld_cannot_be_escaped_with_a_script_tag_in_the_title(self):
        self.post.title = 'Evil </script><script>alert(1)</script>'
        self.post.save(update_fields=['title'])

        html = self.response_html()

        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003c/script\\u003e', html)

    def test_featured_image_is_not_lazy_loaded(self):
        self.post.thumbnail = image_upload()
        self.post.save()

        html = self.response_html()
        featured = re.search(r'<figure class="featured-image">.*?</figure>', html, re.DOTALL).group(0)

        self.assertIn('loading="eager"', featured)
        self.assertIn('fetchpriority="high"', featured)
        self.assertNotIn('loading="lazy"', featured)
        self.assertIn('alt=', featured)

    def test_related_post_images_keep_lazy_loading(self):
        related = make_post(title='Related One', slug='related-one')
        related.thumbnail = image_upload('related.jpg')
        related.save()

        response = self.client.get(self.url)

        self.assertContains(response, 'loading="lazy"')


@override_settings(SITE_URL=SITE)
class PostStatusAndRobotsTests(TestCase):
    def setUp(self):
        self.post = make_post(title='Status Post', slug='status-post')
        self.url = self.post.get_absolute_url()

    def test_missing_post_returns_404(self):
        self.assertEqual(self.client.get('/movies/does-not-exist/').status_code, 404)

    def test_unpublished_post_returns_404(self):
        self.post.is_published = False
        self.post.save(update_fields=['is_published'])

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_wrong_category_slug_redirects_permanently(self):
        response = self.client.get('/wrong-category/status-post/')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.url)

    def test_valid_post_returns_200(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_search_results_are_noindex(self):
        for url in ('/search/?q=movie', '/?q=movie'):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('<meta name="robots" content="noindex, follow">', html)

    def test_post_pages_are_indexable(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('noindex', html)

    def test_login_and_register_pages_are_noindex(self):
        for url in (reverse('login'), reverse('register')):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('<meta name="robots" content="noindex, follow">', html)

    def test_robots_txt_points_at_the_canonical_sitemap(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn(f'Sitemap: {SITE}/sitemap.xml', body)
        self.assertIn('Disallow: /accounts/', body)
        self.assertIn('Disallow: /control-center/', body)
        self.assertIn('Disallow: /search', body)
        self.assertNotIn('hypeblog9jatv.com.ng', body)
        self.assertNotIn('Disallow: /\n', body)

    def test_sitemap_lists_published_posts_on_the_canonical_host(self):
        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertIn(f'{SITE}{self.url}', body)
        self.assertNotIn('/search', body)
        self.assertNotIn('example.com', body)

    def test_sitemap_excludes_unpublished_posts(self):
        self.post.is_published = False
        self.post.save(update_fields=['is_published'])

        body = self.client.get('/sitemap.xml').content.decode()

        self.assertNotIn(self.url, body)


@override_settings(SITE_URL=SITE)
class SiteWideSeoTests(TestCase):
    def test_homepage_has_exactly_one_h1(self):
        html = self.client.get('/').content.decode()

        self.assertEqual(len(re.findall(r'<h1[^>]*>', html)), 1)

    def test_homepage_has_website_json_ld(self):
        html = self.client.get('/').content.decode()
        blocks = [json.loads(match) for match in JSON_LD_RE.findall(html)]
        node_types = [node['@type'] for block in blocks for node in block.get('@graph', [])]

        self.assertIn('WebSite', node_types)
        self.assertIn('Organization', node_types)

    def test_homepage_canonical_is_the_site_root(self):
        html = self.client.get('/').content.decode()

        self.assertIn(f'<link rel="canonical" href="{SITE}/" />', html)

    def test_homepage_og_type_is_website(self):
        html = self.client.get('/').content.decode()

        self.assertIn('<meta property="og:type" content="website">', html)

    def test_category_page_has_one_h1_and_unique_title(self):
        make_post(title='Category Post', slug='category-post')
        html = self.client.get(reverse('category', args=['movies'])).content.decode()

        self.assertEqual(len(re.findall(r'<h1[^>]*>', html)), 1)
        self.assertEqual(page_title(html), 'Movies - Watch & Download - Nzdworld')

    def test_list_pagination_keeps_a_self_referencing_canonical(self):
        for index in range(13):
            make_post(title=f'Paged {index}', slug=f'paged-{index}')

        html = self.client.get('/?page=2').content.decode()

        self.assertIn(f'<link rel="canonical" href="{SITE}/?page=2" />', html)
