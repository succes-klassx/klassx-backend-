"""
Auto-cancels sessions that haven't reached `min_students` by their
`min_enrollment_deadline` (spec 5.1). Run via cron, e.g. hourly:
    0 * * * * cd /path/to/klassx_backend && venv/bin/python manage.py cancel_undersubscribed_sessions

Any already-paid enrollment on a cancelled session is refunded in full,
regardless of timing — this cancellation is KLASSX's fault, not the
student's, so spec 5.2's late-cancellation-no-refund rule doesn't apply
here at all (see core/services/payments.py: refund_enrollment_if_paid).
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ClassSession, Enrollment
from core.services import notifications, payments


class Command(BaseCommand):
    help = "Cancels sessions that didn't reach their minimum enrollment threshold in time, refunding anyone already paid."

    def handle(self, *args, **options):
        now = timezone.now()
        due_sessions = ClassSession.objects.filter(
            status__in=[ClassSession.Status.SCHEDULED, ClassSession.Status.ASSIGNED],
            min_students__isnull=False,
            min_enrollment_deadline__lte=now,
        )

        cancelled_count = 0
        refunded_count = 0
        for session in due_sessions:
            if session.confirmed_seats_taken >= session.min_students:
                continue

            session.status = ClassSession.Status.CANCELLED
            session.save(update_fields=["status"])

            affected = Enrollment.objects.filter(class_session=session, cancelled_at__isnull=True)
            for enrollment in affected:
                enrollment.cancelled_at = now
                enrollment.cancellation_reason = "minimum_enrollment_not_reached"
                enrollment.save(update_fields=["cancelled_at", "cancellation_reason"])
                notifications.send_cancellation_confirmation(enrollment)
                if payments.refund_enrollment_if_paid(enrollment):
                    refunded_count += 1

            cancelled_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Cancelled {cancelled_count} under-subscribed session(s), refunded {refunded_count} payment(s).")
        )
