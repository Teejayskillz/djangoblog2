from .utils import user_has_active_subscription
from .models import PaymentTransaction

def subscription_context(request):
    """
    Context processor providing subscription status, pending transfer state,
    and staff admin pending payment notifications globally across all pages.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'user_has_subscription': False,
            'user_subscription': None,
            'user_has_pending_payment': False,
            'user_pending_transaction': None,
            'admin_pending_payments_count': 0,
        }

    sub = getattr(request.user, 'subscription', None)
    is_active = user_has_active_subscription(request.user)
    pending_tx = PaymentTransaction.objects.filter(user=request.user, status='pending').first()

    admin_pending_count = 0
    if request.user.is_staff:
        admin_pending_count = PaymentTransaction.objects.filter(status='pending').count()

    return {
        'user_has_subscription': is_active,
        'user_subscription': sub if (sub and sub.is_valid()) else None,
        'user_has_pending_payment': bool(pending_tx),
        'user_pending_transaction': pending_tx,
        'admin_pending_payments_count': admin_pending_count,
    }

