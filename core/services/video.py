"""
Live video room provisioning.

Provider order:
1. **The assigned teacher's own setup** (autonomous scheduling model — see
   TeacherProfile.default_meeting_url / google_oauth_refresh_token):
   a. If they've connected their own Google account, a real Calendar
      event + Meet link is created directly on THEIR calendar (see
      core/services/google_meet.py, `teacher=` argument).
   b. Otherwise, if they've set a personal `default_meeting_url` (any
      provider — Meet, Zoom, Teams...), that link is reused as-is, no API
      call needed.
2. **Google Meet via the org-wide organizer** (via Calendar API), if
   `GOOGLE_SERVICE_ACCOUNT_FILE`/`GOOGLE_OAUTH_REFRESH_TOKEN` and
   `GOOGLE_WORKSPACE_ORGANIZER_EMAIL` are set — see `core/services/
   google_meet.py`. Used as a fallback when the assigned teacher hasn't
   set up their own link yet (or no teacher is assigned at all, e.g. a
   session still awaiting assignment).
3. **Daily.co**, if `DAILY_API_KEY` is set — kept as a further fallback.
4. **Jitsi** (meet.jit.si), no key needed — last-resort fallback so
   "Rejoindre / Démarrer" always works, even with nothing configured.

`create_room_for_session` always returns just the meeting URL (string), for
backward compatibility with existing callers. Use
`create_room_for_session_full` instead when you also want the Calendar
event id (needed later to fetch the recording) — that's what
`assign_teacher` / the group-assignment schedule action / weekly series
occurrence generation all call.
"""
import hashlib

import requests
from django.conf import settings

from . import google_meet

DAILY_API_BASE = "https://api.daily.co/v1"


def create_room_for_session_full(class_session):
    """
    Creates a video room for a class session. Returns (meeting_url,
    calendar_event_id) — calendar_event_id is None unless Google Meet was
    used (needed later to look up the recording).
    """
    teacher = class_session.assigned_teacher
    if teacher is not None:
        if teacher.google_connected:
            try:
                meet_url, event_id = google_meet.create_room_for_session(class_session, teacher=teacher)
                return meet_url, event_id
            except google_meet.GoogleMeetError:
                # Fall through to their pasted link, then the org-wide
                # fallbacks below, rather than blocking scheduling.
                pass
        if teacher.default_meeting_url:
            return teacher.default_meeting_url, None

    google_meet_configured = settings.GOOGLE_WORKSPACE_ORGANIZER_EMAIL and (
        settings.GOOGLE_SERVICE_ACCOUNT_FILE or settings.GOOGLE_OAUTH_REFRESH_TOKEN
    )
    if google_meet_configured:
        try:
            meet_url, event_id = google_meet.create_room_for_session(class_session)
            return meet_url, event_id
        except google_meet.GoogleMeetError:
            # Fall through to Daily.co/Jitsi rather than blocking teacher
            # assignment — same "don't block, retry later" philosophy as
            # the rest of this module. Consider logging this in production.
            pass

    if settings.DAILY_API_KEY:
        return _create_daily_room(class_session), None

    return _jitsi_fallback_url(class_session), None


def create_room_for_session(class_session):
    """Backward-compatible shortcut: just the URL, no calendar event id."""
    return create_room_for_session_full(class_session)[0]


def _create_daily_room(class_session):
    response = requests.post(
        f"{DAILY_API_BASE}/rooms",
        headers={"Authorization": f"Bearer {settings.DAILY_API_KEY}"},
        json={
            "name": f"klassx-session-{class_session.id}",
            "properties": {
                "exp": int(class_session.end_time.timestamp()),
                "enable_chat": True,
                "max_participants": class_session.max_capacity + 1,  # + teacher
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["url"]


def _jitsi_fallback_url(class_session):
    """A real, working meeting link with no external account required."""
    raw = f"klassx-session-{class_session.id}-{settings.SECRET_KEY}"
    room_slug = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"https://meet.jit.si/klassx-{room_slug}"
