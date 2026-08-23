"""
Runs every recurring background job KLASSX needs, in one shot — a
convenience wrapper for environments where registering five separate cron
lines/scheduled tasks is awkward or unavailable (e.g. most managed
platforms only give you "run this one command every N minutes", not a
real crontab).

    */5 * * * * cd /path/to/klassx_backend && venv/bin/python manage.py run_scheduled_jobs

Every job below is idempotent and cheap to re-run (see each one's own
docstring for details — most start with a filter like `already_exists`,
`recording_url=""`, or a deadline check), so calling all of them on the
same short interval is safe. There's no need to configure five different
schedules on five different frequencies; running everything every 5
minutes comfortably covers the fastest-changing job (reminders) without
meaningfully over-working the slower ones (e.g. checking for a new weekly
series occurrence every 5 minutes instead of just once a day costs one
cheap "does this exist yet" query).

If you DO have real cron/systemd-timer access and would rather run each
job on its own more accurate schedule instead (e.g. the weekly series
generation only truly needs to run once a day), see
`deploy/crontab.example` for the original per-command frequencies — use
either this wrapper or that file, not both, to avoid jobs racing each
other.

A failure in one job (e.g. Stripe or Google being temporarily down) is
logged but does NOT stop the remaining jobs from running.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand

# Order matters a little: occurrences should exist before reminders look
# for upcoming sessions to remind about, and departures should finalize
# before the next occurrence re-enrolls members — but since each job only
# looks at the current DB state and every one is safe to re-run, a
# slightly "stale" order here just means it catches up on the next run
# rather than causing any incorrect behavior.
JOBS = [
    "generate_series_occurrences",
    "finalize_series_departures",
    "cancel_undersubscribed_sessions",
    "send_session_reminders",
    "fetch_meet_recordings",
]


class Command(BaseCommand):
    help = "Runs all of KLASSX's recurring background jobs once, in sequence. Safe to run frequently — every job is idempotent."

    def handle(self, *args, **options):
        failures = []
        for job_name in JOBS:
            self.stdout.write(self.style.MIGRATE_HEADING(f"--- {job_name} ---"))
            try:
                call_command(job_name)
            except Exception as exc:  # noqa: BLE001 - one job's failure must not block the rest
                failures.append(job_name)
                self.stderr.write(self.style.ERROR(f"{job_name} failed: {exc}"))

        if failures:
            self.stdout.write(self.style.WARNING(f"Done, but {len(failures)} job(s) failed: {', '.join(failures)}"))
        else:
            self.stdout.write(self.style.SUCCESS("All scheduled jobs completed successfully."))
