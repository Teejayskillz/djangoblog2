import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.template.context import BaseContext

from .models import Category, HomepageSection, Post


# Compatibility patch for Python 3.14 test client template context copying
# (same patch as controlcenter/tests.py; needed so this module can run alone).
def _basecontext_copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.dicts = self.dicts[:]
    return duplicate


BaseContext.__copy__ = _basecontext_copy

PAGINATION_BLOCK_RE = re.compile(r'<ul class="pagination-list">(.*?)</ul>', re.DOTALL)

# Django's template lexer uses re.compile(r"({%.*?%}|{{.*?}}|{#.*?#})") *without*
# re.DOTALL, so a tag whose opening delimiter has no closing delimiter on the
# same line is emitted as literal text in the browser.
TAG_PAIRS = (('{{', '}}'), ('{%', '%}'), ('{#', '#}'))
SKIPPED_BLOCKS_RE = re.compile(
    r'{%\s*(?:verbatim|comment)\s*%}.*?{%\s*end(?:verbatim|comment)\s*%}|{#.*?#}',
    re.DOTALL,
)


def _template_directories():
    directories = {Path(settings.BASE_DIR) / 'templates'}
    for app_config in apps.get_app_configs():
        candidate = Path(app_config.path) / 'templates'
        if candidate.is_dir():
            directories.add(candidate)
    return sorted(directories)


class TemplateTagIntegrityTests(TestCase):
    """Guards against template tags that silently render as literal text."""

    def test_no_template_tag_is_split_across_lines(self):
        problems = []
        for directory in _template_directories():
            for path in directory.rglob('*.html'):
                source = path.read_text(encoding='utf-8')
                # Blank out verbatim/comment blocks but keep line numbers.
                source = SKIPPED_BLOCKS_RE.sub(
                    lambda match: re.sub(r'[^\n]', ' ', match.group(0)), source
                )
                for number, line in enumerate(source.splitlines(), 1):
                    for opener, closer in TAG_PAIRS:
                        index = 0
                        while True:
                            start = line.find(opener, index)
                            if start == -1:
                                break
                            end = line.find(closer, start + 2)
                            if end == -1:
                                problems.append(
                                    f'{path.name}:{number} unclosed {opener} -> {line.strip()[:80]}'
                                )
                                break
                            index = end + 2
                    if '}}' in line and '{{' not in line:
                        problems.append(
                            f'{path.name}:{number} stray }} -> {line.strip()[:80]}'
                        )
        self.assertEqual(problems, [], 'Malformed template tags:\n' + '\n'.join(problems))


class HomepagePaginationTests(TestCase):
    """The homepage pager must render real page numbers and links."""

    def setUp(self):
        user = get_user_model().objects.create_user(
            username='pager', email='pager@example.com', password='pass'
        )
        category = Category.objects.create(name='Movies', slug='movies')
        # 12 posts per page -> 60 posts = 5 pages. Five pages matters: only from
        # the 5th page onwards does the pager take its "last page" branch, which
        # is the branch that used to print the literal text "{{ num }}".
        Post.objects.bulk_create([
            Post(
                title=f'Movie {index}',
                slug=f'movie-{index}',
                content='<p>body</p>',
                author=user,
                category=category,
                is_published=True,
            )
            for index in range(60)
        ])

    def _pagination_markup(self, url='/'):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        match = PAGINATION_BLOCK_RE.search(response.content.decode('utf-8'))
        self.assertIsNotNone(match, 'homepage pagination block was not rendered')
        return match.group(1)

    def test_pagination_contains_no_unrendered_template_tags(self):
        for url in ('/', '/?page=1', '/?page=3', '/?page=5'):
            with self.subTest(url=url):
                markup = self._pagination_markup(url)
                self.assertNotIn('{{', markup)
                self.assertNotIn('}}', markup)
                self.assertNotIn('{%', markup)
                self.assertNotIn('%}', markup)

    def test_first_page_links_the_last_page_number(self):
        # The final page link was the one that used to print "{{ num }}".
        markup = self._pagination_markup('/')
        self.assertIn('?page=2', markup)
        self.assertIn('?page=5', markup)
        self.assertIn('>5</a>', markup)
        self.assertIn('Next', markup)

    def test_last_page_marks_itself_current(self):
        markup = self._pagination_markup('/?page=5')
        self.assertIn('?page=1', markup)
        self.assertIn('Previous', markup)
        self.assertIn('>5</span>', markup)


class HomepageSectionTests(TestCase):
    """Section cards must link to a post detail URL that actually reverses."""

    def test_section_posts_link_to_their_category_and_slug(self):
        user = get_user_model().objects.create_user(
            username='sectionuser', email='section@example.com', password='pass'
        )
        category = Category.objects.create(name='News', slug='news')
        Post.objects.create(
            title='News Item',
            slug='news-item',
            content='<p>body</p>',
            author=user,
            category=category,
            is_published=True,
        )
        section = HomepageSection.objects.create(title='Latest News', enabled=True)
        section.categories.set([category])

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/news/news-item/"', response.content.decode('utf-8'))
