"""One-time (but safe to re-run) seed of PricingRate rows from the hardcoded defaults in core/pricing.py — run this once so an admin has editable rows to start from."""
from django.core.management.base import BaseCommand

from core.models import PricingRate
from core.pricing import _DEFAULT_RATES_CENTS, _DEFAULT_RATES_MILLIMES_TND


class Command(BaseCommand):
    help = (
        "Creates any missing PricingRate rows from the default rates in core/pricing.py "
        "(EUR + Tunisia/TND). Never overwrites an existing (already customized) rate — but "
        "does fill in the Tunisia price on a pre-existing row if it was never set (0), e.g. "
        "rows created before Tunisia pricing was added."
    )

    def handle(self, *args, **options):
        created = 0
        completed_tnd = 0
        for group_tier, price_per_hour_cents in _DEFAULT_RATES_CENTS.items():
            rate, was_created = PricingRate.objects.get_or_create(
                group_tier=group_tier,
                defaults={
                    "price_per_hour_cents": price_per_hour_cents,
                    "price_per_hour_millimes_tnd": _DEFAULT_RATES_MILLIMES_TND.get(group_tier, 0),
                },
            )
            if was_created:
                created += 1
            elif not rate.price_per_hour_millimes_tnd:
                rate.price_per_hour_millimes_tnd = _DEFAULT_RATES_MILLIMES_TND.get(group_tier, 0)
                rate.save(update_fields=["price_per_hour_millimes_tnd"])
                completed_tnd += 1
        self.stdout.write(self.style.SUCCESS(
            f"Done — {created} rate(s) created, {completed_tnd} existing rate(s) completed with a "
            f"Tunisia price, rest left untouched."
        ))
