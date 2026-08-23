"""
Sends "your class starts soon" reminders (spec 5.6).

Run this every ~5 minutes via cron / a scheduled task, e.g.:
    */5 * * * * cd /path/to/klassx_backend && venv/bin/python manage.py send_session_reminders

It sends two reminders per enrollment: one around 24h before the session,
one around 10 minutes before. A narrow time window is used for each so the
command can run frequently without spamming duplicate reminders.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Enrollment
from core.services import notifications

WINDOW_MINUTES = 5


class Command(BaseCommand):
    help = "Sends 24h and 10-minute reminder emails for upcoming class sessions."

    def handle(self, *args, **options):
        now = timezone.now()
        sent = 0

        for hours_before, label in [(24, "24h"), (10 / 60, "10min")]:
            target_start = now + timedelta(hours=hours_before)
            window_start = target_start - timedelta(minutes=WINDOW_MINUTES)
            window_end = target_start + timedelta(minutes=WINDOW_MINUTES)

            enrollments = Enrollment.objects.select_related("class_session", "student").filter(
                waitlisted=False,
                cancelled_at__isnull=True,
                class_session__start_time__range=(window_start, window_end),
                class_session__status__in=["scheduled", "assigned"],
            )
            for enrollment in enrollments:
                notifications.send_session_reminder(enrollment, label)
                sent += 1

        self.stdout.write(self.style.SUCCESS(f"Sent {sent} reminder email(s)."))
