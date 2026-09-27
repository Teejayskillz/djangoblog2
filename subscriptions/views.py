from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from .models import SubscriptionPlan, UserSubscription, PaymentTransaction, BankAccount
from .forms import ManualPaymentForm
from .utils import user_has_active_subscription


def plan_list(request):
    """
    Displays all available active subscription plans.
    """
    plans = SubscriptionPlan.objects.filter(is_active=True).order_by('display_order', 'price')
    current_sub = None
    has_sub = False
    pending_transaction = None

    if request.user.is_authenticated:
        has_sub = user_has_active_subscription(request.user)
        current_sub = getattr(request.user, 'subscription', None)
        pending_transaction = PaymentTransaction.objects.filter(
            user=request.user,
            status='pending'
        ).first()

    context = {
        'plans': plans,
        'has_sub': has_sub,
        'current_sub': current_sub,
        'pending_transaction': pending_transaction,
    }
    return render(request, 'subscriptions/plans.html', context)


@login_required
def checkout(request, plan_slug):
    """
    Direct bank transfer checkout for a chosen subscription plan.
    Displays the bank details and accepts transfer proof/receipt upload.
    """
    plan = get_object_or_404(SubscriptionPlan, slug=plan_slug, is_active=True)
    bank_account = BankAccount.get_active_account()

    # Check if user already has an active pending payment
    pending_tx = PaymentTransaction.objects.filter(
        user=request.user,
        plan=plan,
        status='pending'
    ).first()

    if request.method == 'POST':
        form = ManualPaymentForm(request.POST, request.FILES)
        if form.is_valid():
            transaction = form.save(commit=False)
            transaction.user = request.user
            transaction.plan = plan
            transaction.reference = PaymentTransaction.generate_reference()
            transaction.amount = plan.price
            transaction.currency = plan.currency
            transaction.payment_method = 'bank_transfer'
            transaction.status = 'pending'
            transaction.save()

            messages.success(
                request,
                f"🎉 Transfer receipt submitted successfully! (Reference: {transaction.reference}). "
                "Our admin team will review and activate your VIP subscription shortly."
            )
            return redirect('subscriptions:profile')
        else:
            messages.error(request, "Please correct the errors below and try again.")
    else:
        form = ManualPaymentForm()

    context = {
        'plan': plan,
        'bank_account': bank_account,
        'form': form,
        'pending_tx': pending_tx,
    }
    return render(request, 'subscriptions/checkout.html', context)


@login_required
def profile(request):
    """
    User dashboard displaying subscription status, pending transfers, and past transactions.
    """
    sub = getattr(request.user, 'subscription', None)
    transactions = PaymentTransaction.objects.filter(user=request.user).order_by('-created_at')[:20]
    is_active = user_has_active_subscription(request.user)
    pending_transaction = PaymentTransaction.objects.filter(
        user=request.user,
        status='pending'
    ).first()

    context = {
        'subscription': sub,
        'is_active': is_active,
        'pending_transaction': pending_transaction,
        'transactions': transactions,
    }
    return render(request, 'subscriptions/profile.html', context)
