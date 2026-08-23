"""Retries pushing to Brevo any NewsletterSubscriber not yet marked synced_to_brevo (e.g. after a Brevo outage) — safe to run on a schedule (cron) since it's a no-op once everything is synced."""
from django.core.management.base import BaseCommand

from core.models import NewsletterSubscriber
from core.services import brevo


class Command(BaseCommand):
    help = "Pushes any not-yet-synced newsletter subscribers to Brevo. Safe to re-run — only touches rows where synced_to_brevo is False."

    def handle(self, *args, **options):
        if not brevo.is_configured():
            self.stdout.write(self.style.WARNING("BREVO_API_KEY n'est pas configurée — rien à faire."))
            return

        pending = NewsletterSubscriber.objects.filter(synced_to_brevo=False)
        synced, failed = 0, 0
        for subscriber in pending:
            try:
                brevo.add_contact(subscriber.email)
                subscriber.synced_to_brevo = True
                subscriber.save(update_fields=["synced_to_brevo"])
                synced += 1
            except brevo.BrevoError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{subscriber.email}: {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Terminé — {synced} synchronisé(s), {failed} échec(s)."))
