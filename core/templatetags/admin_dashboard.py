from django import template
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

register = template.Library()

@register.inclusion_tag('admin/dashboard_stats.html', takes_context=True)
def dashboard_stats(context):
    """Gather simple statistics and recent items for the admin dashboard."""
    Post = apps.get_model('core', 'Post')
    SportsMatch = None
    try:
        SportsMatch = apps.get_model('sports', 'SportsMatch')
    except LookupError:
        SportsMatch = None
    Subscriber = apps.get_model('core', 'Subscriber')
    Page = apps.get_model('core', 'Page')
    Media = apps.get_model('core', 'Media')
    Ad = None
    try:
        Ad = apps.get_model('ads', 'Ad')
    except LookupError:
        Ad = None
    Comment = apps.get_model('core', 'Comment')

    users_count = get_user_model().objects.count()
    posts_count = Post.objects.count()
    subscribers_count = Subscriber.objects.count()
    pages_count = Page.objects.count()
    media_count = Media.objects.count()
    comments_count = Comment.objects.count()
    ads_count = Ad.objects.count() if Ad else 0
    matches_count = SportsMatch.objects.count() if SportsMatch else 0

    recent_posts = Post.objects.order_by('-published_date')[:6]
    recent_matches = SportsMatch.objects.order_by('-created_at')[:6] if SportsMatch else []
    recent_comments = Comment.objects.order_by('-created_at')[:6]

    return {
        'users_count': users_count,
        'posts_count': posts_count,
        'subscribers_count': subscribers_count,
        'pages_count': pages_count,
        'media_count': media_count,
        'comments_count': comments_count,
        'ads_count': ads_count,
        'matches_count': matches_count,
        'recent_posts': recent_posts,
        'recent_matches': recent_matches,
        'recent_comments': recent_comments,
        'request': context.get('request'),
        'app_list': context.get('app_list'),
    }