"""
Shared helpers for core test modules (deliberately not named test*.py so the
test runner does not collect it as a test module).
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.template.context import BaseContext
from django.utils import timezone

from .models import Category, Comment, Post


# Compatibility patch for Python 3.14 test client template context copying.
# Django's Context.__copy__ calls super().__copy__() on BaseContext, which does
# not exist here, so provide it. The instance __dict__ must be preserved
# (Context.template, render_context, ...) or template inheritance breaks.
def _basecontext_copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.__dict__ = self.__dict__.copy()
    duplicate.dicts = self.dicts[:]
    return duplicate


BaseContext.__copy__ = _basecontext_copy

User = get_user_model()


def make_user(username='commenter', password='pass', **extra):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password=password, **extra
    )


def make_post(title='Test Movie', slug='test-movie', **extra):
    author = extra.pop('author', None) or make_user(f'author-{slug}')
    category = extra.pop('category', None) or Category.objects.get_or_create(
        name='Movies', slug='movies'
    )[0]
    defaults = {
        'content': '<p>Body copy for the post.</p>',
        'author': author,
        'category': category,
        'is_published': True,
    }
    defaults.update(extra)
    return Post.objects.create(title=title, slug=slug, **defaults)


def make_comment(post, user=None, text='A thoughtful comment.', approved=True, name=None):
    return Comment.objects.create(
        post=post,
        user=user,
        name=name or (user.username if user else 'Guest'),
        email=(user.email if user else ''),
        comment=text,
        is_approved=approved,
    )


def give_subscription(user, days=30, is_active=True):
    """Attach a subscription using the project's existing model."""
    from subscriptions.models import SubscriptionPlan, UserSubscription

    plan, _ = SubscriptionPlan.objects.get_or_create(
        slug='vip-test', defaults={'name': 'VIP Test', 'duration_days': 30}
    )
    return UserSubscription.objects.create(
        user=user,
        plan=plan,
        start_date=timezone.now() - timedelta(days=1),
        end_date=timezone.now() + timedelta(days=days),
        is_active=is_active,
    )
