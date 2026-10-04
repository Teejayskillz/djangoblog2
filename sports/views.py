from django.shortcuts import render, get_object_or_404, redirect
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db.models import Q, F
from subscriptions.utils import user_has_active_subscription
from .models import SportsMatch


def sports_list(request):
    """
    Displays all published sports streaming matches with pagination and filtering.
    """
    queryset = SportsMatch.objects.filter(is_published=True).order_by('-is_featured', '-match_date', '-created_at')

    # Filter by sport category if provided
    sport_filter = request.GET.get('sport')
    if sport_filter:
        queryset = queryset.filter(sport__iexact=sport_filter)

    # Search filter
    query = request.GET.get('q')
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(team_home__icontains=query) |
            Q(team_away__icontains=query) |
            Q(sport__icontains=query)
        ).distinct()

    # Pagination (12 matches per page)
    paginator = Paginator(queryset, 12)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Distinct sports list for filter tabs
    available_sports = SportsMatch.objects.filter(is_published=True).values_list('sport', flat=True).distinct()

    context = {
        'page_obj': page_obj,
        'matches': page_obj.object_list,
        'query': query,
        'sport_filter': sport_filter,
        'available_sports': available_sports,
        'total_matches': paginator.count,
    }
    return render(request, 'sports/sports_list.html', context)


def sports_detail(request, slug):
    """
    Displays the detail page for a specific sports match.
    Enforces strict backend VIP subscription verification before passing embed code.
    """
    match = get_object_or_404(SportsMatch, slug=slug, is_published=True)

    # Increment view counter efficiently
    SportsMatch.objects.filter(pk=match.pk).update(views=F('views') + 1)
    match.refresh_from_db()

    # Check VIP subscription status on the server side
    is_vip = user_has_active_subscription(request.user)

    # STRICT ACCESS CONTROL: Embed code is ONLY included in context if user is valid VIP
    safe_embed_code = match.embed_code if is_vip else None

    related_matches = SportsMatch.objects.filter(
        is_published=True
    ).exclude(id=match.id).order_by('-match_date', '-created_at')[:4]

    context = {
        'match': match,
        'is_vip': is_vip,
        'embed_code': safe_embed_code,
        'related_matches': related_matches,
    }
    return render(request, 'sports/sports_detail.html', context)
