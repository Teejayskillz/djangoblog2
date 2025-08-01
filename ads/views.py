# ads/views.py
from django.shortcuts import get_object_or_404, redirect
from django.http import HttpResponse
from .models import Ad

def ad_click_track(request, pk):
    """
    Tracks an ad click and redirects to the ad's target URL.
    """
    ad = get_object_or_404(Ad, pk=pk)
    ad.clicks += 1
    ad.save()

    if ad.target_url:
        return redirect(ad.target_url)
    else:
        # If no target_url, maybe redirect to homepage or show a message
        return HttpResponse("Ad clicked, but no target URL specified.", status=200)

# You might add other views here later, e.g., for impression tracking via AJAX
