from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import SubscriptionPlan, UserSubscription, PaymentTransaction, BankAccount


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ('bank_name', 'account_number', 'account_name', 'is_active', 'updated_at')
    list_editable = ('is_active',)
    list_filter = ('is_active', 'bank_name')
    search_fields = ('bank_name', 'account_number', 'account_name')
    fieldsets = (
        (None, {
            'fields': ('bank_name', 'account_number', 'account_name', 'is_active')
        }),
        ('Checkout Instructions', {
            'fields': ('instructions',),
            'description': 'Text shown to users above the payment receipt upload form.'
        }),
    )


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'currency', 'duration_days', 'badge_text', 'is_active', 'display_order')
    list_editable = ('price', 'is_active', 'display_order')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name', 'description')
    list_filter = ('is_active', 'currency')
    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'price', 'currency', 'duration_days', 'badge_text')
        }),
        ('Details & Perks', {
            'fields': ('description', 'features'),
            'description': 'Enter benefits, one perk per line.'
        }),
        ('Visibility & Ordering', {
            'fields': ('is_active', 'display_order')
        }),
    )


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'plan', 'start_date', 'end_date', 'is_active', 'status_badge', 'days_left')
    list_filter = ('is_active', 'plan', 'start_date', 'end_date')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')
    actions = ['activate_selected', 'deactivate_selected', 'extend_by_30_days']

    def status_badge(self, obj):
        if obj.is_valid():
            return format_html('<span style="color: #10b981; font-weight: bold;">● Active VIP</span>')
        return format_html('<span style="color: #ef4444; font-weight: bold;">● Inactive/Expired</span>')
    status_badge.short_description = 'Status'

    def days_left(self, obj):
        days = obj.days_remaining
        if days > 0:
            return f"{days} days"
        return "Expired"
    days_left.short_description = 'Time Remaining'

    @admin.action(description="Activate selected subscriptions")
    def activate_selected(self, request, queryset):
        from .emails import send_subscription_activated_email
        for sub in queryset:
            sub.is_active = True
            sub.save()
            send_subscription_activated_email(user=sub.user, plan=sub.plan, subscription=sub, request=request)
        self.message_user(request, "Selected subscriptions have been activated.")

    @admin.action(description="Deactivate selected subscriptions")
    def deactivate_selected(self, request, queryset):
        queryset.update(is_active=False)
        self.message_user(request, "Selected subscriptions have been deactivated.")

    @admin.action(description="Add 30 bonus days to selected subscriptions")
    def extend_by_30_days(self, request, queryset):
        from .emails import send_subscription_activated_email
        for sub in queryset:
            from datetime import timedelta
            now = timezone.now()
            if sub.end_date and sub.end_date > now:
                sub.end_date += timedelta(days=30)
            else:
                sub.end_date = now + timedelta(days=30)
            sub.is_active = True
            sub.save()
            send_subscription_activated_email(user=sub.user, plan=sub.plan, subscription=sub, request=request)
        self.message_user(request, "Added 30 days to selected subscriptions.")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = (
        'reference',
        'user',
        'plan',
        'formatted_amount',
        'receipt_preview',
        'user_notes_short',
        'status_badge',
        'created_at'
    )
    list_filter = ('status', 'plan', 'created_at')
    search_fields = ('reference', 'user__username', 'user__email', 'user_notes', 'admin_notes')
    readonly_fields = ('reference', 'receipt_full_preview', 'created_at', 'updated_at')
    actions = ['approve_and_activate', 'reject_payment']

    fieldsets = (
        ('Transaction Details', {
            'fields': ('reference', 'user', 'plan', 'amount', 'currency', 'status')
        }),
        ('User Transfer Proof & Info', {
            'fields': ('proof_of_payment', 'receipt_full_preview', 'user_notes')
        }),
        ('Admin Review Notes', {
            'fields': ('admin_notes',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def formatted_amount(self, obj):
        return f"{obj.currency} {obj.amount:,.2f}"
    formatted_amount.short_description = 'Amount'

    def user_notes_short(self, obj):
        if not obj.user_notes:
            return "-"
        return obj.user_notes[:40] + ("..." if len(obj.user_notes) > 40 else "")
    user_notes_short.short_description = 'Depositor Info'

    def status_badge(self, obj):
        colors = {
            'completed': '#10b981',
            'pending': '#f59e0b',
            'failed': '#ef4444',
            'cancelled': '#6b7280'
        }
        color = colors.get(obj.status, '#374151')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: bold; text-transform: uppercase;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'

    def receipt_preview(self, obj):
        if obj.proof_of_payment:
            ext = obj.proof_of_payment.name.lower()
            if ext.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                return format_html(
                    '<a href="{}" target="_blank">'
                    '<img src="{}" style="height: 50px; width: 50px; object-fit: cover; border-radius: 6px; border: 1px solid #4B5563;" />'
                    '</a>',
                    obj.proof_of_payment.url,
                    obj.proof_of_payment.url
                )
            return format_html('<a href="{}" target="_blank">📄 View PDF/Doc</a>', obj.proof_of_payment.url)
        return format_html('<span style="color: #9CA3AF;">No receipt</span>')
    receipt_preview.short_description = 'Receipt'

    def receipt_full_preview(self, obj):
        if obj.proof_of_payment:
            ext = obj.proof_of_payment.name.lower()
            if ext.endswith(('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                return format_html(
                    '<div style="margin: 10px 0;">'
                    '<a href="{}" target="_blank">'
                    '<img src="{}" style="max-width: 400px; max-height: 400px; border-radius: 8px; border: 1px solid #4B5563; display: block; margin-bottom: 5px;" />'
                    'Click image to open full size'
                    '</a></div>',
                    obj.proof_of_payment.url,
                    obj.proof_of_payment.url
                )
            return format_html('<p><a href="{}" target="_blank" class="button">📄 Open Uploaded Document</a></p>', obj.proof_of_payment.url)
        return "No receipt uploaded."
    receipt_full_preview.short_description = 'Receipt Preview'

    def save_model(self, request, obj, form, change):
        if change and 'status' in form.changed_data and obj.status == 'completed':
            obj.complete_transaction(admin_user=request.user)
        else:
            super().save_model(request, obj, form, change)

    @admin.action(description="✅ Approve & Activate VIP Subscription")
    def approve_and_activate(self, request, queryset):
        approved = 0
        for tx in queryset:
            tx.complete_transaction(admin_user=request.user)
            approved += 1
        self.message_user(request, f"Successfully approved {approved} transfer(s) and activated VIP access for the user(s).")

    @admin.action(description="❌ Reject selected payment(s)")
    def reject_payment(self, request, queryset):
        for tx in queryset:
            tx.reject_transaction(admin_user=request.user, reason="Declined by administrator")
        self.message_user(request, "Selected payments marked as rejected.")
