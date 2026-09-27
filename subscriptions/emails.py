import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.db import models

logger = logging.getLogger(__name__)


def get_all_admin_emails():
    """
    Returns a unique list of email addresses for all active superusers and staff members,
    plus any emails defined in settings.ADMINS.
    """
    User = get_user_model()
    admin_emails = set()

    try:
        db_emails = User.objects.filter(
            models.Q(is_superuser=True) | models.Q(is_staff=True),
            is_active=True
        ).exclude(email__isnull=True).exclude(email__exact='').values_list('email', flat=True)

        for e in db_emails:
            cleaned = e.strip()
            if cleaned and '@' in cleaned:
                admin_emails.add(cleaned)
    except Exception as err:
        logger.error(f"Error fetching admin emails from database: {err}")

    admins_setting = getattr(settings, 'ADMINS', [])
    for entry in admins_setting:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            e = entry[1]
        elif isinstance(entry, str):
            e = entry
        else:
            e = None
        if e and '@' in e:
            admin_emails.add(e.strip())

    return list(admin_emails)


def send_receipt_uploaded_notifications(transaction, request=None):
    """
    Sends email notifications when a user uploads a payment receipt:
    1. Confirmation email to the user that their receipt was received.
    2. Alert email to ALL site admin email addresses requesting VIP approval.
    """
    if not transaction:
        return

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@nzdworld.com')
    user = transaction.user
    plan_name = transaction.plan.name if transaction.plan else "VIP Subscription"
    currency = transaction.currency or "NGN"
    amount = f"{transaction.amount:,.2f}" if transaction.amount else "0.00"

    admin_url = None
    if request:
        try:
            admin_url = request.build_absolute_uri(f"/admin/subscriptions/paymenttransaction/{transaction.id}/change/")
        except Exception:
            admin_url = f"/admin/subscriptions/paymenttransaction/{transaction.id}/change/"

    # 1. Send receipt upload confirmation email to user
    if user and user.email:
        try:
            subject = f"Payment Receipt Received - Ref: {transaction.reference}"
            context = {
                'username': user.username,
                'plan_name': plan_name,
                'reference': transaction.reference,
                'currency': currency,
                'amount': amount,
                'user_notes': transaction.user_notes,
            }
            html_content = render_to_string('subscriptions/emails/receipt_uploaded_user.html', context)
            text_content = strip_tags(html_content)

            msg = EmailMultiAlternatives(subject, text_content, from_email, [user.email])
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=True)
            logger.info(f"Sent payment receipt upload email to user {user.email}")
        except Exception as e:
            logger.error(f"Failed to send receipt upload email to user {user.email}: {e}")

    # 2. Send alert email to ALL admin emails
    admin_emails = get_all_admin_emails()
    if admin_emails:
        try:
            subject = f"[Admin Alert] New Payment Receipt Uploaded by {user.username if user else 'User'} - {transaction.reference}"
            context = {
                'username': user.username if user else "Unknown User",
                'user_email': user.email if user else "N/A",
                'plan_name': plan_name,
                'reference': transaction.reference,
                'currency': currency,
                'amount': amount,
                'user_notes': transaction.user_notes,
                'admin_url': admin_url,
            }
            html_content = render_to_string('subscriptions/emails/receipt_uploaded_admin.html', context)
            text_content = strip_tags(html_content)

            msg = EmailMultiAlternatives(subject, text_content, from_email, admin_emails)
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=True)
            logger.info(f"Sent new payment alert email to admins: {admin_emails}")
        except Exception as e:
            logger.error(f"Failed to send payment alert email to admins: {e}")
    else:
        logger.warning("No admin emails found to send payment alert to.")


def send_subscription_activated_email(user, plan=None, subscription=None, transaction=None, request=None):
    """
    Sends an email notification to the user when their VIP subscription plan has been activated.
    """
    if not user or not user.email:
        return

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@nzdworld.com')
    plan_name = plan.name if plan else (subscription.plan.name if (subscription and subscription.plan) else "VIP Subscription")

    start_date = "N/A"
    end_date = "N/A"
    if subscription:
        if subscription.start_date:
            start_date = subscription.start_date.strftime('%B %d, %Y')
        if subscription.end_date:
            end_date = subscription.end_date.strftime('%B %d, %Y')

    site_url = "/"
    if request:
        try:
            site_url = request.build_absolute_uri('/')
        except Exception:
            pass

    try:
        subject = f"🎉 Your VIP Subscription is Now Active! - {plan_name}"
        context = {
            'username': user.username,
            'plan_name': plan_name,
            'start_date': start_date,
            'end_date': end_date,
            'site_url': site_url,
        }
        html_content = render_to_string('subscriptions/emails/subscription_activated.html', context)
        text_content = strip_tags(html_content)

        msg = EmailMultiAlternatives(subject, text_content, from_email, [user.email])
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=True)
        logger.info(f"Sent subscription activation email to user {user.email}")
    except Exception as e:
        logger.error(f"Failed to send subscription activation email to user {user.email}: {e}")
