from django.core.management.base import BaseCommand
from subscriptions.models import SubscriptionPlan, BankAccount


class Command(BaseCommand):
    help = "Seeds default subscription plans and bank account details for NZDWORLD"

    def handle(self, *args, **options):
        # 1. Seed Bank Account if none exists
        if not BankAccount.objects.filter(is_active=True).exists():
            bank = BankAccount.objects.create(
                bank_name="OPay / Moniepoint / GTBank",
                account_number="9012345678",
                account_name="NZDWORLD ENTERPRISES",
                instructions="Make a direct bank transfer of the plan amount to the account details above. Write your username as transfer remark/narration, then upload your receipt below for manual admin verification.",
                is_active=True
            )
            self.stdout.write(self.style.SUCCESS(f"Created default Bank Account: {bank}"))
        else:
            self.stdout.write("Active Bank Account already configured.")

        # 2. Seed Subscription Plans
        plans_data = [
            {
                "name": "VIP Monthly",
                "slug": "vip-monthly",
                "price": 1500.00,
                "currency": "NGN",
                "duration_days": 30,
                "description": "30 days of complete ad-free entertainment with high-speed download access.",
                "features": "100% Ad-Free Browsing\nUnlocked VIP Download Links\nExclusive VIP Account Badge\nStandard Support",
                "badge_text": "",
                "display_order": 1,
                "is_active": True,
            },
            {
                "name": "VIP Quarterly (3 Months)",
                "slug": "vip-quarterly",
                "price": 4000.00,
                "currency": "NGN",
                "duration_days": 90,
                "description": "Best value for frequent movie and series fans. Save over 10% on your VIP access.",
                "features": "100% Ad-Free Browsing\nUnlocked VIP High-Speed Links\nExclusive VIP Account Badge\nPriority Movie & Series Requests\nTelegram VIP Community Access",
                "badge_text": "Most Popular",
                "display_order": 2,
                "is_active": True,
            },
            {
                "name": "VIP Annual Pass",
                "slug": "vip-annual-pass",
                "price": 14000.00,
                "currency": "NGN",
                "duration_days": 365,
                "description": "Full 365 days uninterrupted VIP experience. Maximum savings for loyal members.",
                "features": "100% Ad-Free Browsing Everywhere\nUnlocked Ultra High-Speed Downloads\nExclusive VIP Gold Badge\nInstant Direct Movie Requests\n24/7 Dedicated VIP Support",
                "badge_text": "Best Value",
                "display_order": 3,
                "is_active": True,
            },
        ]

        created_count = 0
        for data in plans_data:
            plan, created = SubscriptionPlan.objects.get_or_create(
                slug=data["slug"],
                defaults=data
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"Created plan: {plan.name}"))
            else:
                self.stdout.write(f"Plan already exists: {plan.name}")

        self.stdout.write(self.style.SUCCESS(f"Done! Created {created_count} new plan(s)."))

