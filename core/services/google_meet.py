"""
Google Meet room provisioning, via the Google Calendar API.

Why Calendar API and not the newer Google Meet REST API directly: creating
a Calendar event with `conferenceData` is the simplest, fully-GA way to get
a real Meet link tied to a real Workspace account, and it gives students
and teachers a calendar invite "for free" (with the reminder + join button
they already know from Gmail/Calendar). The dedicated Meet REST API does
exist and can also create bare meeting spaces (see `spaces.create`), and
its `SpaceConfig.ArtifactConfig` even supports requesting auto-recording —
but that piece is still in Google's "Developer Preview" program at the time
this was written, so it isn't used here to avoid depending on something
that may need separate enrollment/approval on your Workspace. Swap in the
Meet API later if you want that (see the note in `README.md`).

Auth model: **OAuth 2.0 "installed app" credentials** (Client ID + Client
Secret from Google Cloud Console), authorized once against one fixed
"organizer" Workspace mailbox (`GOOGLE_WORKSPACE_ORGANIZER_EMAIL`, e.g.
cours@your-domain.fr). Every KLASSX session is created as a Calendar event
owned by that one mailbox, with the teacher and enrolled students added as
guests. (A service-account-with-domain-wide-delegation is normally the
simpler setup for this — no per-session token refresh, no one-time consent
screen — but Google Cloud orgs can enforce the
`iam.managed.disableServiceAccountKeyCreation` policy, which blocks
downloading a service account key entirely; if that's the case for your
org, OAuth Client credentials are the supported fallback, at the cost of
one manual one-time authorization step — see
`python manage.py get_google_oauth_token` below.)

Recording itself is NOT started by this code — Google doesn't offer a
supported way to force-start recording on an ordinary Workspace Meet call
from a server (see README). Two options, configured entirely in the Google
Admin console, no code changes needed:
  1. Turn on Meet's organization-wide "auto-recording" setting for the
     relevant Workspace organizational unit, so every call organized by the
     `GOOGLE_WORKSPACE_ORGANIZER_EMAIL` mailbox is recorded automatically.
  2. Leave it manual and have the teacher click "Enregistrer" from inside
     Meet, same as any Google Workspace user would.
Either way, once a recording finishes processing it lands in the
organizer's Drive ("Meet Recordings" folder); `fetch_meet_recordings`
(management command) uses the Google Meet API's `conferenceRecords`
endpoint to find it and save the link onto `ClassSession.recording_url`.
"""
import uuid

from django.conf import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    # Needed only by fetch_meet_recordings (reading conference artifacts).
    "https://www.googleapis.com/auth/meetings.space.readonly",
]

# Extra scopes requested only during the interactive per-teacher "Connect
# your Google account" web flow (see views.TeacherGoogleConnectView) — adds
# just enough to read back the authorized account's email address for
# display on the teacher's dashboard. Not needed for the org-level
# organizer flow, so kept separate from SCOPES above.
TEACHER_CONNECT_SCOPES = SCOPES + [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GoogleMeetError(Exception):
    """Raised when a Meet room could not be provisioned or looked up."""


def credentials_for_teacher(teacher):
    """
    Builds credentials scoped to one teacher's OWN connected Google account
    (autonomous scheduling model — see TeacherProfile.google_oauth_refresh_token
    and views.TeacherGoogleConnectView/TeacherGoogleCallbackView). Returns
    None if this teacher hasn't connected an account, or if the shared
    OAuth Client (GOOGLE_OAUTH_CLIENT_ID/SECRET — the same Client used for
    the connect flow) isn't configured.
    """
    from google.oauth2.credentials import Credentials

    if not teacher or not teacher.google_oauth_refresh_token:
        return None
    if not (settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET):
        return None

    return Credentials(
        token=None,
        refresh_token=teacher.google_oauth_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )


def _load_credentials():
    """
    Builds credentials for the organizer mailbox. Tries a service account
    first (GOOGLE_SERVICE_ACCOUNT_FILE) — the simpler option when your org
    allows it — and falls back to OAuth Client + refresh token
    (GOOGLE_OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN) otherwise. Exactly one of
    the two needs to be configured.
    """
    if settings.GOOGLE_SERVICE_ACCOUNT_FILE:
        return _load_service_account_credentials()
    if settings.GOOGLE_OAUTH_REFRESH_TOKEN:
        return _load_oauth_credentials()
    raise GoogleMeetError(
        "Neither GOOGLE_SERVICE_ACCOUNT_FILE nor GOOGLE_OAUTH_REFRESH_TOKEN "
        "is configured — see README.md Google Meet setup."
    )


def _load_service_account_credentials():
    from google.oauth2 import service_account

    if not settings.GOOGLE_WORKSPACE_ORGANIZER_EMAIL:
        raise GoogleMeetError("GOOGLE_WORKSPACE_ORGANIZER_EMAIL is not configured.")

    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES,
    )
    # Domain-wide delegation: act *as* the organizer mailbox rather than as
    # the bare service account (which has no Calendar/Drive of its own).
    return credentials.with_subject(settings.GOOGLE_WORKSPACE_ORGANIZER_EMAIL)


def _load_oauth_credentials():
    from google.oauth2.credentials import Credentials

    missing = [
        name for name in (
            "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN",
        ) if not getattr(settings, name)
    ]
    if missing:
        raise GoogleMeetError(f"Missing settings for OAuth mode: {', '.join(missing)}")

    # No access_token here on purpose: passing refresh_token with no
    # access_token forces the client library to fetch a fresh access token
    # on first use via the refresh_token — so this never goes stale.
    return Credentials(
        token=None,
        refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=SCOPES,
    )

    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES,
    )
    # Domain-wide delegation: act *as* the organizer mailbox rather than as
    # the bare service account (which has no Calendar/Drive of its own).
    return credentials.with_subject(settings.GOOGLE_WORKSPACE_ORGANIZER_EMAIL)


def _calendar_service(teacher=None):
    from googleapiclient.discovery import build

    teacher_creds = credentials_for_teacher(teacher) if teacher is not None else None
    credentials = teacher_creds if teacher_creds is not None else _load_credentials()
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def create_room_for_session(class_session, teacher=None):
    """
    Creates a Calendar event with an attached Google Meet link for a
    ClassSession, invites the assigned teacher (and enrolled students, if
    any exist yet), and returns (meeting_url, calendar_event_id).

    `teacher`: pass a TeacherProfile with a connected Google account (see
    TeacherProfile.google_oauth_refresh_token) to create the event on THAT
    teacher's own calendar instead of the fixed org organizer mailbox —
    this is the autonomous-scheduling path (see core/services/video.py).
    Defaults to the org organizer when omitted or the teacher isn't
    connected, same as before.

    Raises GoogleMeetError on any failure — callers should catch this and
    fall back to another provider (see core/services/video.py), same as
    the existing Daily.co error handling.
    """
    try:
        service = _calendar_service(teacher=teacher)
    except Exception as exc:  # noqa: BLE001 - surfaced as GoogleMeetError
        raise GoogleMeetError(f"Could not build Calendar client: {exc}") from exc

    organizer_is_teacher = teacher is not None and credentials_for_teacher(teacher) is not None
    attendees = []
    if (
        not organizer_is_teacher
        and class_session.assigned_teacher_id
        and class_session.assigned_teacher.user.email
    ):
        # When the event is created on the org organizer's calendar, invite
        # the assigned teacher as a guest. When it's created directly on
        # the teacher's OWN calendar (organizer_is_teacher), they're
        # already the organizer — no need to add them as an attendee too.
        attendees.append({"email": class_session.assigned_teacher.user.email})
    for enrollment in class_session.enrollments.select_related("student").all():
        if enrollment.student.email:
            attendees.append({"email": enrollment.student.email})

    event_body = {
        "summary": f"KLASSX — {class_session.subject.name} ({class_session.get_group_tier_display()})",
        "description": (
            "Cours en ligne KLASSX. Rejoignez via le lien Google Meet ci-dessous, "
            "ou depuis votre tableau de bord KLASSX."
        ),
        "start": {"dateTime": class_session.start_time.isoformat()},
        "end": {"dateTime": class_session.end_time.isoformat()},
        "attendees": attendees,
        "conferenceData": {
            "createRequest": {
                "requestId": f"klassx-session-{class_session.id}-{uuid.uuid4().hex[:8]}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        },
        "guestsCanSeeOtherGuests": False,
    }

    try:
        event = service.events().insert(
            calendarId="primary",
            body=event_body,
            conferenceDataVersion=1,
            sendUpdates="all",
        ).execute()
    except Exception as exc:  # noqa: BLE001
        raise GoogleMeetError(f"Calendar API event creation failed: {exc}") from exc

    meet_url = None
    for entry_point in event.get("conferenceData", {}).get("entryPoints", []):
        if entry_point.get("entryPointType") == "video":
            meet_url = entry_point.get("uri")
            break

    if not meet_url:
        raise GoogleMeetError("Calendar event was created but no Meet link was returned.")

    return meet_url, event["id"]


def update_attendees(class_session, teacher=None):
    """
    Re-syncs the guest list on an already-created event (call this after
    new students enroll, so they get a calendar invite too). No-op if the
    session has no calendar_event_id (i.e. it isn't a Google Meet room).

    `teacher`: same meaning as in create_room_for_session — defaults to
    class_session.assigned_teacher, which is correct as long as the
    teacher assignment hasn't changed since the event was created.
    """
    if not class_session.calendar_event_id:
        return
    if teacher is None:
        teacher = class_session.assigned_teacher

    service = _calendar_service(teacher=teacher)
    organizer_is_teacher = credentials_for_teacher(teacher) is not None
    attendees = []
    if not organizer_is_teacher and class_session.assigned_teacher_id and class_session.assigned_teacher.user.email:
        attendees.append({"email": class_session.assigned_teacher.user.email})
    for enrollment in class_session.enrollments.select_related("student").all():
        if enrollment.student.email:
            attendees.append({"email": enrollment.student.email})

    service.events().patch(
        calendarId="primary",
        eventId=class_session.calendar_event_id,
        body={"attendees": attendees},
        sendUpdates="all",
    ).execute()


def fetch_recording_url(class_session, teacher=None):
    """
    Looks up the Meet recording for a finished session via the Google Meet
    API's conferenceRecords, matching by the event's Meet space. Returns the
    Drive URL, or None if no recording is available yet (still processing,
    recording wasn't enabled, or session hasn't happened).

    `teacher`: same meaning as in create_room_for_session — defaults to
    class_session.assigned_teacher.
    """
    from googleapiclient.discovery import build

    if not class_session.calendar_event_id:
        return None
    if teacher is None:
        teacher = class_session.assigned_teacher

    calendar = _calendar_service(teacher=teacher)
    event = calendar.events().get(calendarId="primary", eventId=class_session.calendar_event_id).execute()
    conference_id = event.get("conferenceData", {}).get("conferenceId")
    if not conference_id:
        return None

    teacher_creds = credentials_for_teacher(teacher)
    credentials = teacher_creds if teacher_creds is not None else _load_credentials()
    meet = build("meet", "v2", credentials=credentials, cache_discovery=False)
    # conferenceRecords are addressed by meeting-code space name, not the
    # Calendar conferenceId directly, so we look up the space first.
    space = meet.spaces().get(name=f"spaces/{conference_id}").execute()
    records = meet.conferenceRecords().list(
        filter=f'space.name="{space["name"]}"'
    ).execute()

    for record in records.get("conferenceRecords", []):
        recordings = meet.conferenceRecords().recordings().list(parent=record["name"]).execute()
        for recording in recordings.get("recordings", []):
            drive_uri = recording.get("driveDestination", {}).get("exportUri")
            if drive_uri:
                return drive_uri
    return None
