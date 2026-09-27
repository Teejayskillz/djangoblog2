from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta
import uuid


class BankAccount(models.Model):
    """
    Configurable bank account where users should make their transfer payments.
    Managed directly by the admin in Django Admin.
    """
    bank_name = models.CharField(max_length=100, help_text="Bank Name (e.g. OPay, Moniepoint, GTBank, Access Bank)")
    account_number = models.CharField(max_length=30, help_text="Account Number for transfers")
    account_name = models.CharField(max_length=150, help_text="Account Name matching the bank account")
    instructions = models.TextField(
        blank=True,
        default="Make a direct bank transfer to the account details above. Enter your username or phone number in the transfer narration/remark. Then upload your transfer receipt below for fast verification.",
        help_text="Custom instructions displayed to the user on the checkout page"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this account is currently shown on the checkout page")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bank Account Detail"
        verbose_name_plural = "Bank Account Details"
        ordering = ['-is_active', '-created_at']

    def __str__(self):
        status = "Active" if self.is_active else "Inactive"
        return f"{self.bank_name} - {self.account_number} ({self.account_name}) [{status}]"

    @classmethod
    def get_active_account(cls):
        active = cls.objects.filter(is_active=True).first()
        if active:
            return active
        # Fallback if none created yet
        return cls(
            bank_name=getattr(settings, 'BANK_NAME', 'OPay / Palmpay / Moniepoint'),
            account_number=getattr(settings, 'ACCOUNT_NUMBER', '9012345678'),
            account_name=getattr(settings, 'ACCOUNT_NAME', 'NZDWORLD ENTERPRISES'),
            instructions="Make a direct bank transfer to the account details above. Enter your username in the transfer narration/remark. Then upload your transfer receipt below for fast verification."
        )


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=100, help_text="e.g. VIP Monthly, VIP Yearly")
    slug = models.SlugField(max_length=100, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    currency = models.CharField(max_length=10, default="NGN", help_text="e.g. NGN, USD, EUR")
    duration_days = models.PositiveIntegerField(default=30, help_text="Duration of access in days (e.g. 30, 90, 365)")
    description = models.TextField(blank=True, help_text="Brief summary of what this plan offers")
    features = models.TextField(
        blank=True,
        help_text="Perks separated by line, e.g.:\n100% Ad-Free Browsing\nUltra-Fast Download Links\nExclusive VIP Badge\nPriority Customer Support"
    )
    badge_text = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        help_text="Optional highlight badge like 'Most Popular' or 'Best Value'"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this plan is visible to users")
    display_order = models.PositiveIntegerField(default=0, help_text="Order in which plans appear on the pricing page")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'price']
        verbose_name = "Subscription Plan"
        verbose_name_plural = "Subscription Plans"

    def __str__(self):
        return f"{self.name} ({self.formatted_price()} / {self.duration_days} days)"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def formatted_price(self):
        symbols = {
            'NGN': '₦',
            'USD': '$',
            'EUR': '€',
            'GBP': '£',
        }
        symbol = symbols.get(self.currency.upper(), f"{self.currency} ")
        if self.price == int(self.price):
            return f"{symbol}{int(self.price):,}"
        return f"{symbol}{self.price:,.2f}"

    def get_features_list(self):
        if not self.features:
            return []
        return [f.strip() for f in self.features.splitlines() if f.strip()]


class UserSubscription(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='subscription'
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='user_subscriptions'
    )
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Subscription"
        verbose_name_plural = "User Subscriptions"

    def __str__(self):
        status = "Active" if self.is_valid() else "Expired"
        return f"{self.user.username} - {self.plan.name if self.plan else 'Custom'} ({status})"

    def is_valid(self):
        return bool(self.is_active and self.end_date and self.end_date >= timezone.now())

    @property
    def days_remaining(self):
        if not self.is_valid():
            return 0
        delta = self.end_date - timezone.now()
        return max(0, delta.days)

    def extend_subscription(self, plan):
        now = timezone.now()
        if self.is_valid() and self.end_date > now:
            self.end_date = self.end_date + timedelta(days=plan.duration_days)
        else:
            self.start_date = now
            self.end_date = now + timedelta(days=plan.duration_days)
        self.plan = plan
        self.is_active = True
        self.save()


class PaymentTransaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('completed', 'Approved & Active'),
        ('failed', 'Declined / Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('bank_transfer', 'Manual Bank Transfer'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        related_name='transactions'
    )
    reference = models.CharField(max_length=100, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    payment_method = models.CharField(
        max_length=50,
        choices=PAYMENT_METHOD_CHOICES,
        default='bank_transfer'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    proof_of_payment = models.FileField(
        upload_to='payment_proofs/',
        blank=True,
        null=True,
        help_text="Uploaded bank transfer receipt/screenshot"
    )
    user_notes = models.TextField(
        blank=True,
        null=True,
        help_text="Notes provided by user (e.g. sender bank, account name, narration)"
    )
    admin_notes = models.TextField(
        blank=True,
        null=True,
        help_text="Internal notes or reasons for approval/rejection"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Payment Transaction"
        verbose_name_plural = "Payment Transactions"

    def __str__(self):
        return f"{self.reference} - {self.user.username} - {self.get_status_display()}"

    @classmethod
    def generate_reference(cls):
        return f"SUB-{uuid.uuid4().hex[:10].upper()}"

    def complete_transaction(self, admin_user=None):
        if self.status == 'completed':
            return False

        self.status = 'completed'
        if admin_user:
            note = f"Approved by admin '{admin_user.username}' on {timezone.now().strftime('%Y-%m-%d %H:%M')}"
            self.admin_notes = f"{self.admin_notes or ''}\n{note}".strip()
        self.save()

        # Update or create user subscription
        sub, created = UserSubscription.objects.get_or_create(
            user=self.user,
            defaults={
                'plan': self.plan,
                'start_date': timezone.now(),
                'end_date': timezone.now() + timedelta(days=self.plan.duration_days if self.plan else 30),
                'is_active': True
            }
        )
        if not created and self.plan:
            sub.extend_subscription(self.plan)

        return True

    def reject_transaction(self, admin_user=None, reason=""):
        self.status = 'failed'
        if admin_user:
            note = f"Rejected by admin '{admin_user.username}' on {timezone.now().strftime('%Y-%m-%d %H:%M')}"
            if reason:
                note += f": {reason}"
            self.admin_notes = f"{self.admin_notes or ''}\n{note}".strip()
        self.save()
        return True
