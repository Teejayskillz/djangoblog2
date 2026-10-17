
from django.core.cache import cache
from django.conf import settings


def ads_context(request):
    """
    Makes active ad content available to templates.

    Active subscribers get no ads at all (VIP ad-free experience); for everyone
    else the ad payload (site-wide, not personalised) is cached briefly so a
    page render does not query the ads table on every request.
    """
    from subscriptions.utils import user_has_active_subscription
    from .models import Ad

    if hasattr(request, 'user') and user_has_active_subscription(request.user):
        return {'ads_by_slug': {}}

    cache_key = 'cc:ads:v1'
    ads_by_slug = cache.get(cache_key)
    if ads_by_slug is not None:
        return {'ads_by_slug': ads_by_slug}

    ads_by_slug = {}
    try:
        # Fetch all active ads from the database
        active_ads = Ad.objects.filter(is_active=True).only('slug', 'ad_content')
        for ad in active_ads:
            # Store the ad content, marked as safe, using its slug as the key
            ads_by_slug[ad.slug] = ad.ad_content
        cache.set(cache_key, ads_by_slug, int(getattr(settings, 'ADS_CACHE_SECONDS', 60)))
    except Exception as e:
        # Log any errors that occur during ad retrieval
        print(f"Error fetching ads for context processor: {e}")
        # In case of an error, return an empty dictionary to prevent template errors
        ads_by_slug = {}
    return {'ads_by_slug': ads_by_slug}
