def user_has_active_subscription(user):
    """
    Returns True if the user is authenticated and has an active, non-expired subscription.
    """
    if not user or not user.is_authenticated:
        return False

    # Superusers / staff can optionally be treated as having access if desired,
    # but let's check for explicit subscription or superuser
    if user.is_superuser:
        return True

    subscription = getattr(user, 'subscription', None)
    if subscription and subscription.is_valid():
        return True

    return False
