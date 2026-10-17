"""
Comment service helpers.

Keeps the moderation rules, the public read queryset and the anti-spam checks
in one place so views, admin and tests agree on the behaviour.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Comment

DEFAULT_MIN_INTERVAL = 20      # seconds between two comments by the same user
DEFAULT_MAX_PER_HOUR = 30      # comments per user per rolling hour
DEFAULT_DUPLICATE_WINDOW = 10  # minutes in which identical text is rejected


def comments_per_page():
    return int(getattr(settings, 'COMMENTS_PER_PAGE', 20))


def visible_comments(post):
    """
    Approved comments for one post, newest first.

    select_related keeps display_name and the premium badge (which reads the
    commenter's subscription) from issuing a query per comment.
    """
    return (
        Comment.objects.filter(post=post, is_approved=True)
        .select_related('user', 'user__subscription')
        .order_by('-created_at')
    )


def can_post_comment(user, post, text):
    """
    Lightweight, database-backed anti-spam checks.

    Returns (allowed, message). Staff are exempt so moderators are never
    throttled while answering comments.
    """
    if not user or not user.is_authenticated:
        return False, 'You must be logged in to comment.'

    if user.is_staff:
        return True, ''

    now = timezone.now()
    min_interval = int(getattr(settings, 'COMMENT_MIN_INTERVAL_SECONDS', DEFAULT_MIN_INTERVAL))
    max_per_hour = int(getattr(settings, 'COMMENT_MAX_PER_HOUR', DEFAULT_MAX_PER_HOUR))
    duplicate_window = int(
        getattr(settings, 'COMMENT_DUPLICATE_WINDOW_MINUTES', DEFAULT_DUPLICATE_WINDOW)
    )

    recent = (
        Comment.objects.filter(user=user).order_by('-created_at').only('created_at').first()
    )
    if recent and min_interval > 0:
        elapsed = (now - recent.created_at).total_seconds()
        if elapsed < min_interval:
            wait = max(1, int(min_interval - elapsed))
            return False, f'You are commenting too quickly. Please wait {wait} more second(s).'

    if max_per_hour > 0:
        hour_ago = now - timedelta(hours=1)
        if Comment.objects.filter(user=user, created_at__gte=hour_ago).count() >= max_per_hour:
            return False, 'You have reached the comment limit for this hour. Please try again later.'

    if duplicate_window > 0:
        duplicate = Comment.objects.filter(
            user=user,
            post=post,
            comment=text,
            created_at__gte=now - timedelta(minutes=duplicate_window),
        ).exists()
        if duplicate:
            return False, 'You have already posted that comment.'

    return True, ''


def create_comment(user, post, text):
    """
    Persist a comment for the logged-in user.

    Author details come from the account, never from the request body, and new
    comments start unapproved so only moderated comments are public.
    """
    return Comment.objects.create(
        post=post,
        user=user,
        name=(user.get_full_name() or user.username)[:100],
        email=(user.email or '')[:254],
        comment=text,
        is_approved=False,
    )
