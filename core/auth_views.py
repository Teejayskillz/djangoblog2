"""Throttled wrappers around Django's built-in authentication views."""

from django.contrib.auth.views import LoginView, PasswordResetView
from django.shortcuts import render

from controlcenter.throttle import check_rate_limit
from controlcenter.traffic import get_client_ip


def rate_limited_response(request, result, scope):
    """Render the shared 429 page with a Retry-After header."""
    response = render(
        request,
        '429.html',
        {
            'retry_after': result.retry_after,
            'retry_minutes': max(1, round(result.retry_after / 60)),
            'scope': scope,
        },
        status=429,
    )
    response['Retry-After'] = str(result.retry_after)
    return response


def _client_key(request):
    ip = get_client_ip(request)
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return f'{ip}:u{user.pk}'
    return ip


class ThrottledLoginView(LoginView):
    """
    Login view with per-IP and per-IP+username limits.

    The per-username limit keeps credential stuffing from a single IP from
    hammering one account, without locking accounts permanently.
    """

    def dispatch(self, request, *args, **kwargs):
        # Only POSTs count: loading the login page is normal browsing, and
        # throttling GETs would lock out ordinary visitors.
        if request.method == 'POST':
            ip = get_client_ip(request)
            result = check_rate_limit('login', ip, request)
            if not result.allowed:
                return rate_limited_response(request, result, 'login')

            username = (request.POST.get('username') or '').strip().lower()[:150]
            if username:
                by_user = check_rate_limit('login_username', f'{ip}:{username}', request)
                if not by_user.allowed:
                    return rate_limited_response(request, by_user, 'login')

        return super().dispatch(request, *args, **kwargs)


class ThrottledPasswordResetView(PasswordResetView):
    """Password reset sends email, so it is limited per IP."""

    def dispatch(self, request, *args, **kwargs):
        if request.method == 'POST':
            ip = get_client_ip(request)
            result = check_rate_limit('password_reset', ip, request)
            if not result.allowed:
                return rate_limited_response(request, result, 'password_reset')
        return super().dispatch(request, *args, **kwargs)
