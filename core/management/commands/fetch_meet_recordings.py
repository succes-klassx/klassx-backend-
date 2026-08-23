"""
Fetches Google Meet recording links for recently-completed sessions and
stores them on `ClassSession.recording_url`, so students can watch back a
session they missed from their dashboard.

Only does anything for sessions that used Google Meet (i.e. that have a
`calendar_event_id` — see core/services/google_meet.py); sessions on
Daily.co/Jitsi are skipped, since neither is wired up to fetch recordings
here.

Google can take anywhere from a few minutes to a couple of hours to finish
processing a recording after the call ends, so this is meant to be run
periodically rather than once right after a session finishes:
    */15 * * * * cd /path/to/klassx_backend && venv/bin/python manage.py fetch_meet_recordings

Requires recording to have actually been turned on for the call — either
by the teacher clicking "Enregistrer" in Meet, or via an org-wide
auto-recording policy set in the Google Admin console (Apps > Google
Workspace > Google Meet > "Enregistrement des réunions"). This command
only *retrieves* the link; it cannot start a recording that never happened.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ClassSession
from core.services import google_meet


class Command(BaseCommand):
    help = "Fetches Google Meet recording links for completed sessions."

    def handle(self, *args, **options):
        now = timezone.now()
        # Look back a generous window (7 days) since Google's processing
        # delay is unpredictable and this command is idempotent/cheap to
        # re-run against sessions that already have no recording yet.
        candidates = ClassSession.objects.filter(
            end_time__lte=now,
            end_time__gte=now - timedelta(days=7),
            recording_url="",
        ).exclude(calendar_event_id="")

        found_count = 0
        for session in candidates:
            try:
                recording_url = google_meet.fetch_recording_url(session)
            except Exception as exc:  # noqa: BLE001
                self.stderr.write(f"Session {session.id}: lookup failed ({exc})")
                continue

            if recording_url:
                session.recording_url = recording_url
                session.save(update_fields=["recording_url"])
                found_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Checked {candidates.count()} session(s), found {found_count} new recording(s)."
        ))
