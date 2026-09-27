# ads/context_processors.py

from .models import Ad
from subscriptions.utils import user_has_active_subscription

def ads_context(request):
    """
    A custom context processor to make active ad content available globally
    to all templates. Hides all ads for active subscribers (ad-free VIP experience).
    """
    if hasattr(request, 'user') and user_has_active_subscription(request.user):
        return {'ads_by_slug': {}}

    ads_by_slug = {}
    try:
        # Fetch all active ads from the database
        active_ads = Ad.objects.filter(is_active=True)
        for ad in active_ads:
            # Store the ad content, marked as safe, using its slug as the key
            ads_by_slug[ad.slug] = ad.ad_content
    except Exception as e:
        # Log any errors that occur during ad retrieval
        print(f"Error fetching ads for context processor: {e}")
        # In case of an error, return an empty dictionary to prevent template errors
        ads_by_slug = {}
    return {'ads_by_slug': ads_by_slug}

