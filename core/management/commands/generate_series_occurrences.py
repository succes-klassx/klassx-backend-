"""
Generates the next occurrence of each active recurring ClassSeries, and
automatically re-enrolls its continuing members — this is what keeps a
group "fixed" week after week (same students, same teacher) without any
manual re-creation.

Run this weekly via cron, comfortably ahead of each series' next slot, e.g.:
    0 6 * * MON cd /path/to/klassx_backend && venv/bin/python manage.py generate_series_occurrences

Membership (not last-session attendance) now drives continuity: a
SeriesMembership is billed monthly and auto-renews (spec: "si l'élève ne
résilie pas, le renouvellement est automatique"). A member who asked to
leave (`status=LEAVING`) keeps being enrolled — and billed if their
membership is billable — until the start of the following month, when
`leaves_on` passes; see SeriesMembership.is_member_on().
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ClassSeries, ClassSession, Enrollment, SeriesMembership
from core.services import notifications, video


class Command(BaseCommand):
    help = "Creates the next weekly occurrence for each active ClassSeries and re-enrolls its continuing members."

    def handle(self, *args, **options):
        today = timezone.localdate()
        created_count = 0

        for series in ClassSeries.objects.filter(ends_on__gte=today):
            last_session = (
                ClassSession.objects.filter(series=series).order_by("-start_time").first()
            )
            search_from = (last_session.start_time.date() + timedelta(days=1)) if last_session else series.starts_on

            next_date = self._next_weekday_on_or_after(search_from, series.weekday)
            if next_date > series.ends_on:
                continue  # series has run its course

            already_exists = ClassSession.objects.filter(series=series, start_time__date=next_date).exists()
            if already_exists:
                continue

            start_dt = timezone.make_aware(datetime.combine(next_date, series.start_time))
            end_dt = start_dt + timedelta(minutes=series.duration_minutes)

            new_session = ClassSession.objects.create(
                subject=series.subject, level=series.level, group_tier=series.group_tier,
                max_capacity=ClassSession.TIER_CAPACITY[series.group_tier],
                assigned_teacher=series.assigned_teacher, series=series,
                start_time=start_dt, end_time=end_dt,
                status=ClassSession.Status.ASSIGNED if series.assigned_teacher else ClassSession.Status.SCHEDULED,
            )
            try:
                new_session.meeting_url, event_id = video.create_room_for_session_full(new_session)
                if event_id:
                    new_session.calendar_event_id = event_id
                new_session.save(update_fields=["meeting_url", "calendar_event_id"])
            except Exception:
                pass

            # Re-enroll everyone whose membership is still active on this
            # occurrence's date — this is the "fixed group" part. Since the
            # group is billed monthly (not per-session), a member with an
            # active subscription is auto-marked "paid" for this occurrence.
            continuing_memberships = [
                m for m in SeriesMembership.objects.filter(series=series).select_related("student")
                if m.is_member_on(start_dt)
            ]

            for membership in continuing_memberships:
                payment_status = (
                    Enrollment.PaymentStatus.PAID
                    if not membership.is_billable
                    or (membership.status == SeriesMembership.Status.ACTIVE and membership.stripe_subscription_id)
                    else Enrollment.PaymentStatus.PENDING
                )
                enrollment = Enrollment.objects.create(
                    student=membership.student, class_session=new_session, payment_status=payment_status
                )
                notifications.send_enrollment_confirmed(enrollment)

            created_count += 1
            self.stdout.write(f"Created occurrence for series #{series.id} on {next_date} "
                               f"with {len(continuing_memberships)} continuing member(s).")

        self.stdout.write(self.style.SUCCESS(f"Done — {created_count} new occurrence(s) created."))

    @staticmethod
    def _next_weekday_on_or_after(date_, weekday):
        """weekday: 0=Monday ... 6=Sunday (matches Python's date.weekday())."""
        days_ahead = (weekday - date_.weekday()) % 7
        return date_ + timedelta(days=days_ahead)
