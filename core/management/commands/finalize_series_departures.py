"""
Finalizes departures from a fixed recurring group once a student's 2-week
notice period has passed — the current calendar month always runs to
completion unchanged, and changes take effect the 1st of the next month
(spec: "le groupe doit terminer le forfait mensuel sans aucun changement").

Run this daily via cron, e.g.:
    30 6 * * * cd /path/to/klassx_backend && venv/bin/python manage.py finalize_series_departures

Until `leaves_on` passes, the membership stays ACTIVE for billing/enrollment
purposes (see SeriesMembership.is_member_on and generate_series_occurrences)
— this command is what actually stops the subscription and marks the
membership LEFT once that date arrives.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import SeriesMembership
from core.services import payments


class Command(BaseCommand):
    help = "Cancels the Stripe subscription and finalizes status for memberships whose notice period (next month) has arrived."

    def handle(self, *args, **options):
        now = timezone.now()
        due = SeriesMembership.objects.filter(status=SeriesMembership.Status.LEAVING, leaves_on__lte=now)

        finalized = 0
        for membership in due:
            try:
                payments.cancel_stripe_subscription(membership.stripe_subscription_id, at_period_end=False)
            except Exception as exc:
                self.stderr.write(f"Stripe cancellation failed for membership #{membership.id}: {exc}")
                continue
            membership.status = SeriesMembership.Status.LEFT
            membership.save(update_fields=["status"])
            finalized += 1

        self.stdout.write(self.style.SUCCESS(f"Finalized {finalized} departure(s)."))
