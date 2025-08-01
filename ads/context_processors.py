# ads/context_processors.py

from .models import Ad

def ads_context(request):
    """
    A custom context processor to make active ad content available globally
    to all templates.

    This function fetches all active ads and organizes them by their slug
    into a dictionary, which is then added to the template context.

    Args:
        request: The current HttpRequest object.

    Returns:
        dict: A dictionary containing 'ads_by_slug', where keys are ad slugs
              and values are the HTML content of the active ads.
    """
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

