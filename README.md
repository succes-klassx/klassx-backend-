# KLASSX — Backend (Django + Django REST Framework)

This is the first building block of KLASSX: **data models + authentication +
core booking API**, matching the technical specification (sections 1–6).

## ⭐ How booking works (confirmed with the product owner, supersedes the
## original "student picks a time slot" spec)

Students do **not** pick a date/time themselves anymore. Instead:

1. A student submits a **`GroupRequest`**: subject + level (1ère/Terminale)
   + group size (10/5/3) + a **weekly-hour package** (6/8/12/16/24h/week).
   No time slot, no payment yet. **INDIVIDUAL never uses this flow at
   all** — see step 5.
2. The admin dashboard's **"pending requests" view** (`/api/group-requests/pending_summary/`)
   shows how many students are waiting for each subject/level/size/package combo.
3. Once enough matching requests have piled up, the admin calls
   **`/api/admin/assign-group/`** with the selected request IDs and a
   teacher — this creates a **`GroupAssignment`** and hands the group to
   that teacher. **No schedule is picked here.**
4. **The teacher then schedules it themselves**, from their dashboard —
   `POST /api/group-assignments/{id}/schedule/` with one or more weekly
   day/time slots (+ their own meeting link). **`weekly_hours` is the
   package's TOTAL weekly commitment, and usually needs more than one
   slot to reach** (e.g. an 8h/semaine package might be Monday 4h +
   Thursday 4h) — a single class rarely runs for the whole weekly hour
   count in one sitting. This endpoint can be called more than once: the
   first call moves the group from "awaiting schedule" to "scheduled",
   later calls just add more slots. `GroupAssignmentSerializer` exposes
   `scheduled_slots` (every slot set up so far) and
   `scheduled_weekly_minutes`/`target_weekly_minutes`, which is what the
   teacher dashboard uses to show "X h programmées sur Y h/semaine" and
   let the teacher know exactly how many more slots they still need to
   add — see "Autonomous teacher scheduling" below for the full picture,
   including how the meeting link is chosen and how billing avoids
   double-charging across slots.
5. If the group is recurring, **`generate_series_occurrences`** (a scheduled
   command, see section 3) automatically creates each following week's
   session and **carries the same members forward**. **Recurring groups
   are billed monthly with automatic renewal** (`SeriesMembership`), priced
   from the package (`core/pricing.py: package_monthly_price_cents`). The
   current month always runs to completion unchanged — leaving or changing
   a package takes effect on the **1st of the following month**
   (`leave` action + `finalize_series_departures`).
6. **INDIVIDUAL bypasses all of the above.** A student calls
   `/api/individual-bookings/` directly with subject + level + their own
   start/end time, pays immediately via Stripe, and is enrolled on the
   spot — no admin step, no package, no commitment. A teacher is assigned
   afterward the normal way (`assign_teacher`), since the time is already fixed.

### Saved-card billing (group packages) — when the student actually pays

Group packages use a "add your card now, get billed later" model, to avoid
chasing students for payment after the fact:

1. When a student submits a `GroupRequest`, if they don't have a saved
   card yet, the frontend prompts them to add one via
   `POST /api/me/payment-method/setup/` — a Stripe Checkout Session in
   `mode="setup"`. **No charge happens at this point** — it only saves the
   card as the student's default payment method (via the
   `checkout.session.completed` webhook, kind `card_setup`).
2. Nothing is billed while the request sits pending, or even once an
   admin assigns a teacher — still no charge.
3. **The student is only actually charged once their teacher schedules a
   real session** (`GroupAssignmentViewSet.schedule`) — at that exact
   moment, if the student has a saved card, KLASSX creates the real
   monthly Stripe Subscription server-side and charges it immediately
   (off-session — the teacher's action triggers this, not the student's).
   See `core/services/payments.py: charge_saved_payment_method`.
4. If the student has no saved card yet, or the off-session charge is
   declined (expired card, insufficient funds...), scheduling still
   succeeds — the membership just stays `pending`, the student is emailed
   (`send_payment_method_declined` if declined), and the existing manual
   "Payer" button on their dashboard (`SeriesMembershipViewSet.checkout`)
   remains available as a fallback.

This only applies to group packages. **INDIVIDUAL bookings are unaffected**
— they still charge immediately at booking time, since there's no
group-forming/teacher-assignment delay to bridge for those.

### Autonomous teacher scheduling — who picks the link, and how

Once a teacher has a `GroupAssignment` (step 3 above) or is assigned an
individual session, **they own the meeting link** for what they schedule.
`core/services/video.py` resolves it in this order:

1. If the teacher has **connected their own Google account**
   (`TeacherProfile.google_oauth_refresh_token` — see
   `/api/teachers/me/google/connect/`), a real Calendar event + Google Meet
   link is created directly on **their own calendar**.
2. Else, if they've set a **personal link** (`TeacherProfile.default_meeting_url`
   — any provider: Meet, Zoom, Teams... — via `PATCH /api/teachers/me/`),
   that link is reused as-is.
3. Else, falls back to the **org-wide integration** (Google Meet via the
   organizer account / Daily.co / Jitsi — same as before), so a session
   never ends up with no link at all even if the teacher hasn't set
   anything up yet.

**Billing across multiple slots of the same package**: since
`weekly_hours` is the package's total (often split across several weekly
slots — see step 4 above), only the **first slot ever scheduled** for a
`GroupAssignment` is billable; every slot added after that (same call or
a later one) is automatically created as non-billable
(`SeriesMembership.is_billable=False`, `monthly_price_cents=0`) so the
family is never charged more than once for one package. This is handled
automatically by `GroupAssignmentViewSet.schedule` — nothing to configure.

A teacher can also override the link **per session**, by passing an
explicit `meeting_url` to `schedule()` or `add_extra_session()` (see
below) — this always wins over all three fallbacks above.

Two more teacher-only endpoints round this out:
- `POST /api/class-sessions/add_extra_session/` — adds a one-off extra
  session (e.g. a makeup class) to a `ClassSeries` the teacher is assigned
  to, auto-enrolling every currently active member. The regular weekly
  occurrence is still generated automatically by `generate_series_occurrences`
  — this is only for anything outside that normal slot.
- `GET/PATCH /api/teachers/me/` — the teacher's own settings
  (`default_meeting_url`, and read-only `google_connected`/`google_account_email`).

**Setting up the per-teacher Google connect flow** (optional — teachers
can just paste a personal link instead, no setup needed for that):
it reuses the same OAuth Client as `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`
(see "Google Meet / Google Workspace setup" below), but needs **one more
redirect URI** registered in Google Cloud Console, since this flow's
callback is a real backend endpoint rather than a local script:
```
<your backend's public URL>/api/teachers/me/google/callback/
```
(e.g. `http://localhost:8000/api/teachers/me/google/callback/` for local
dev, or your real domain in production). No other setup is needed — a
teacher clicks "Connecter mon compte Google" on their dashboard, and the
rest happens automatically.

The old **`/api/admin/schedule-group/`** (admin picks the request IDs
*and* the date/time *and* the teacher, all in one call) still works and
is kept for manual overrides, but the admin frontend no longer uses it —
see the docstring on `AdminScheduleGroupView` in `core/views.py`.

This solves two things the original "browse a calendar of slots" model
didn't: (a) it avoids fragmenting a handful of early students across many
half-empty time slots, and (b) it guarantees a teacher never gets a
different mix of students from one session to the next.


⚠️ This code was written without a network connection available, so it has
**not been run or migrated yet**. Follow the steps below on your machine to
get it working; if anything doesn't import cleanly, it's most likely a small
typo to fix rather than a structural issue — the models/serializers/views
were written carefully but do need a first real `runserver` pass.

## 1. Setup

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then edit values as needed
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

By default it uses SQLite (zero config). Set `DATABASE_NAME` in `.env` to
switch to PostgreSQL.

## 1bis. Running the tests

```bash
python manage.py test
```

Covers registration (student + teacher), login, password reset,
`/me/specialties/`, `/admin/teacher-hours/`, role-based permissions,
teacher payout computation, parental consent, and the booking/payment
flows (individual bookings, enrollment waitlist/cancel/refund, and the
full group flow: GroupRequest → AdminAssignGroupView → teacher schedule())
— see `core/tests/`. Stripe and the video provider are mocked throughout,
so `python manage.py test` never makes a real network call. Still not
covered: the webhook handler itself, `generate_series_occurrences`, and
`cancel_undersubscribed_sessions` — extend as those get touched.

## 2. What's implemented

| Area | Status |
|---|---|
| Custom `User` model with roles (student/teacher/admin) | ✅ |
| Student self-registration + JWT login | ✅ `/api/auth/register/`, `/api/auth/login/` |
| Teacher self-registration (pending admin approval) | ✅ `/api/auth/register-teacher/` — creates the account right away, but `TeacherProfile.is_active` stays False until `approve` (see `/api/teachers/`) |
| Password reset by email | ✅ `/api/auth/password-reset/` (request) + `/api/auth/password-reset/confirm/` (set new password) |
| Subjects catalog (admin CRUD, everyone reads) | ✅ `/api/subjects/` |
| Teacher profiles, availability, admin approval | ✅ `/api/teachers/`, `approve`/`reject` actions |
| Video capsules + progress tracking | ✅ models + API, **playback gated by active subscription** (`playback_url` action returns 402 if none) |
| Class sessions (group tiers, capacity, admin assigns teacher) | ✅ `/api/class-sessions/`, `assign_teacher`, `mine` (teacher) |
| Enrollments with **waitlist logic** (spec 5.1) | ✅ `/api/enrollments/` |
| Cancellation with notice-period check (spec 5.2) | ✅ `cancel` action — **the 24h threshold is a placeholder, confirm the real value** |
| **Stripe payments** (one-off + subscription checkout, webhook) | ✅ code complete — **needs real Stripe API keys to run**, see section 3 |
| **Konnect payments** (Tunisia — Stripe doesn't support TND) | ✅ code complete — routed automatically when the student's country is "Tunisie" (see `core/services/konnect.py`); **no recurring billing**, monthly group payments must be relaunched by the student each month; **needs real Konnect API keys to run**, see section 3 |
| **Email notifications** (confirmation, waitlist, cancellation, teacher approval, reminders) | ✅ console backend by default — set SMTP/SendGrid vars to send real emails |
| **Video room provisioning** | ✅ **Google Meet** (Calendar API) if `GOOGLE_SERVICE_ACCOUNT_FILE` + `GOOGLE_WORKSPACE_ORGANIZER_EMAIL` are set, else Daily.co if `DAILY_API_KEY` is set, else a **real working Jitsi Meet link** (no key needed) |
| **Session recordings** (Google Meet only) | ✅ `python manage.py fetch_meet_recordings` picks up the Drive link once Google finishes processing it, stores it on `recording_url` — shown to students on their dashboard next to "Rejoindre" |
| Forum (threads, replies, mark solved) | ✅ `/api/forum/threads/`, `/api/forum/replies/` |
| Group content — documents & video links, shared with every student in a group (not just one session), shown on the student dashboard | ✅ `/api/materials/` — `group_assignment` (whole group) or `class_session` (one-off/INDIVIDUAL); teachers can only manage materials for groups/sessions they're actually assigned to |
| Group announcements — messages a teacher posts for their group, shown on the student dashboard + emailed | ✅ `/api/group-announcements/` |
| Admin dashboard stats | ✅ `/api/admin/stats/` |
| Session reminders (24h / 10min before) | ✅ `python manage.py send_session_reminders` — schedule via cron, see section 3 |
| Auto-cancel sessions below `min_students` | ✅ `python manage.py cancel_undersubscribed_sessions` — schedule via cron, see section 3 |
| **Group requests** (students request subject+level+size, admin schedules) | ✅ `/api/group-requests/`, `/api/group-requests/pending_summary/`, `/api/admin/schedule-group/` — replaces the old "student picks a time slot" flow (confirmed with product owner: fixed recurring groups, teacher continuity) |
| **Recurring group continuity** (same students carried forward each week) | ✅ `python manage.py generate_series_occurrences` — schedule via cron, see section 3 |
| **Monthly billing for recurring groups + 2-week notice to leave** | ✅ `SeriesMembership` model, `/api/series-memberships/{id}/checkout/` and `/leave/`, `python manage.py finalize_series_departures` — schedule via cron, see section 3 |
| Default minimum-enrollment thresholds | ✅ confirmed: GROUP_10→5, GROUP_5→3, GROUP_3→2, INDIVIDUAL→none, deadline = 24h before start. Applied automatically on session creation (admin or API) — see `ClassSession.save()` in `core/models.py`. Override per-session in the admin if needed. |

## 3. Activating the real integrations

Everything below works out of the box in a *degraded but non-breaking* way
(console emails, placeholder video links, Stripe calls will error if you try
to use them). To make them fully real:

- **Stripe**: create an account, grab your secret key from
  https://dashboard.stripe.com/apikeys, set `STRIPE_SECRET_KEY`. Create a
  recurring Price for the video subscription and set
  `STRIPE_SUBSCRIPTION_PRICE_ID`. Register a webhook endpoint pointing to
  `/api/webhooks/stripe/` for the `checkout.session.completed` event, and
  set `STRIPE_WEBHOOK_SECRET` from the dashboard. Use `stripe listen
  --forward-to localhost:8000/api/webhooks/stripe/` for local testing.
- **Konnect (Tunisia payments)**: create a merchant account at
  https://konnect.network, grab your API key and wallet id from the
  sandbox dashboard (https://dashboard.sandbox.konnect.network) for
  testing, set `KONNECT_API_KEY` and `KONNECT_WALLET_ID`. Leave
  `KONNECT_SANDBOX=True` until you're ready for production traffic. The
  webhook URL (`/api/webhooks/konnect/`) is registered automatically on
  each payment — no separate dashboard step needed, unlike Stripe.
- **Daily.co (video rooms)**: create an account at daily.co, copy the API
  key from Developers, set `DAILY_API_KEY`. Without it, a real (but
  unbranded) Jitsi Meet link is used instead — no setup needed for testing.
- **Google Meet (recommended — you already have Google Workspace)**: see
  the dedicated section below.

### Google Meet / Google Workspace setup

KLASSX creates every session's video room as a **Google Calendar event**
(owned by one fixed Workspace mailbox) with a Meet link attached — see
`core/services/google_meet.py` for the full reasoning. Setup, one-time:

1. **Google Cloud project**: create one at
   https://console.cloud.google.com (or reuse an existing one), and enable
   the **Google Calendar API** and **Google Meet API** for it
   (APIs & Services > Library).
2. **Service account**: APIs & Services > Credentials > Create credentials
   > Service account. Give it any name (e.g. `klassx-meet`). Create a JSON
   key for it and download it — this is your `GOOGLE_SERVICE_ACCOUNT_FILE`.
   **Do not commit this file** — add it to `.gitignore`.
3. **Domain-wide delegation**: in that service account's details, enable
   "Domain-wide delegation" and note its **Client ID**. Then, as a Google
   Workspace *super admin*, go to
   admin.google.com > Security > API controls > Domain-wide delegation >
   Add new, paste the Client ID, and authorize these scopes:
   ```
   https://www.googleapis.com/auth/calendar
   https://www.googleapis.com/auth/meetings.space.readonly
   ```
4. **Pick an organizer mailbox**: any real, licensed user in your
   Workspace (a dedicated one like `cours@your-domain.fr` is cleanest,
   so recordings/calendar all land in one predictable place rather than a
   specific teacher's personal account). Set `GOOGLE_WORKSPACE_ORGANIZER_EMAIL`
   to that address.
5. Set both env vars and you're done — `assign_teacher` and
   `admin/schedule-group/` will now create real Meet links automatically.

**If your org policy blocks service account key creation** (the
`iam.managed.disableServiceAccountKeyCreation` org policy — you'll see
this as an error when trying to download a key in step 2 above), use the
OAuth Client fallback instead:

1. APIs & Services > Credentials > Create credentials > OAuth client ID.
   Google Cloud lets you create either a **Desktop app** or a **Web
   application** client — either works, but the setup differs slightly:
   - **Desktop app**: download the JSON, save it as `google_credentials.json`
     at the project root, and skip straight to step 2 below — no redirect
     URI setup needed.
   - **Web application**: download the JSON, save it as
     `google_credentials.json` at the project root. Unlike Desktop app
     clients, Google requires you to pre-register the exact redirect URI
     that `get_google_oauth_token` will use. Open the client in Cloud
     Console, under "Authorized redirect URIs" add:
     ```
     http://localhost:8080/
     ```
     Save, and wait a minute for it to propagate.
2. Run, once, from a computer with a browser:
   ```
   python manage.py get_google_oauth_token
   ```
   (it auto-detects Desktop vs Web app from the file, and for Web app
   clients tells you exactly what to fix if the redirect URI isn't
   registered yet). Sign in with the mailbox that should own every
   KLASSX session's Meet room — the same one you'll put in
   `GOOGLE_WORKSPACE_ORGANIZER_EMAIL`.
3. Copy the three values it prints (`GOOGLE_OAUTH_CLIENT_ID`,
   `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN`) into your
   `.env`, along with `GOOGLE_WORKSPACE_ORGANIZER_EMAIL`.
4. Domain-wide delegation is **not needed** in OAuth mode — you
   authorized directly as the organizer mailbox in step 2, so KLASSX
   already has permission to create events as that account.

**About recordings**: this integration does not (and, as far as Google
currently allows for a normal Workspace account, cannot) *start* a
recording from the server. Two ways to actually get recordings, both
config-only, no code:
- **Automatic**: in admin.google.com > Apps > Google Workspace > Google
  Meet > "Enregistrement des réunions", turn on auto-recording for the
  organizational unit your organizer mailbox belongs to. Every KLASSX
  session gets recorded with zero teacher action.
- **Manual**: teachers click "Enregistrer" inside Meet like any Workspace
  user would — fine if you'd rather record only some sessions.

Either way, once Google finishes processing a recording it appears in the
organizer's Drive, and `python manage.py fetch_meet_recordings` (schedule
it via cron every 15 min, see below) finds it and saves the link.
Recording requires a Workspace edition that includes it (Business
Standard and up, or Enterprise) — check your plan if the "Enregistrement"
setting doesn't appear.
- **Email**: point `EMAIL_BACKEND` at
  `django.core.mail.backends.smtp.EmailBackend` and fill in `EMAIL_HOST*`,
  or swap in `django-anymail` for SendGrid/Mailgun.

### Scheduled jobs

Six management commands need to run periodically — **none is scheduled
automatically**; this is infrastructure you set up once at deploy time,
not something Django does on its own. Five of them run frequently and can
share one wrapper (Option A below); `compute_payouts` is monthly-only and
always needs its own line regardless of which option you pick for the
other five — see its docstring for why it's deliberately left out of
`run_scheduled_jobs`. Two ways to handle the five frequent ones:

**Option A — one wrapper command, one schedule.** Simplest, and the only
practical option on platforms that only let you configure "run this one
command every N minutes" (most managed hosts):
```bash
*/5 * * * * cd /path/to/klassx_backend && venv/bin/python manage.py run_scheduled_jobs
0 7 1 * *   cd /path/to/klassx_backend && venv/bin/python manage.py compute_payouts
```
The first line runs all five frequent jobs every 5 minutes — every one of
them is idempotent and cheap to re-run against unchanged data (see each
command's own docstring), so this is safe; it's slightly wasteful for the
once-a-day jobs, but not meaningfully so. `compute_payouts` always needs
its own separate monthly line, on either option — see its docstring for why.

**Option B — real cron, one line per job at its ideal frequency.** Lower
server load; needs actual crontab/systemd-timer access:
```bash
0 6 * * *    cd /path/to/klassx_backend && venv/bin/python manage.py generate_series_occurrences
30 6 * * *   cd /path/to/klassx_backend && venv/bin/python manage.py finalize_series_departures
0 * * * *    cd /path/to/klassx_backend && venv/bin/python manage.py cancel_undersubscribed_sessions
*/5 * * * *  cd /path/to/klassx_backend && venv/bin/python manage.py send_session_reminders
*/15 * * * * cd /path/to/klassx_backend && venv/bin/python manage.py fetch_meet_recordings
0 7 1 * *    cd /path/to/klassx_backend && venv/bin/python manage.py compute_payouts
```
A ready-to-edit copy of this lives at `deploy/crontab.example` — install
with `crontab deploy/crontab.example` after fixing the paths. **Use
Option A or B, not both**, or jobs can race each other.

Platform notes:
- **Render / Railway**: use their "Cron Job" service type, pointed at
  `python manage.py run_scheduled_jobs`, on a `*/5 * * * *` schedule —
  these platforms don't give you real crontab access, so Option A is the
  practical choice.
- **Heroku**: the Scheduler add-on's minimum granularity is 10 minutes,
  which slightly widens the reminder window — still fine given
  `send_session_reminders`' own 5-minute window is just to avoid
  duplicate sends, not a hard deadline.
- **A regular VPS**: either option works; Option B is marginally more
  efficient if you care about shaving cron invocations.
- Cron runs with a minimal environment (no `.env` auto-loaded the way an
  interactive shell might) — make sure `python-dotenv` is installed (see
  `requirements.txt`) and the working directory (`cd
  /path/to/klassx_backend`) is correct so `load_dotenv()` in `settings.py`
  actually finds your `.env`.

## 4. Open business-rule decisions still needed

These are called out with `# TODO` / docstring comments in the code, and map
to section 5 of the spec — nothing here should block development, but the
placeholder values must be confirmed before launch:

- `CANCELLATION_NOTICE_HOURS = 24` in `core/views.py`
- Video capsule access scope: currently **full catalog for any active
  subscriber** (spec 5.3 decision made here) — revisit if you'd rather gate
  by level/chapter instead.
- ✅ Teacher payout calculation is now automated — `python manage.py
  compute_payouts` (monthly, see `deploy/crontab.example`) computes each
  active teacher's `Payout` for a calendar month from their
  `compensation_type`/`compensation_rate`, and the Django admin has a
  "Marquer comme payé" action to close it out once the transfer is sent.
  See the command's docstring for exactly how revenue is worked out for
  `percentage`-type compensation on recurring (subscription-billed)
  groups — it's an estimate reconstructed session-by-session, since
  Stripe only gives KLASSX one monthly charge per group, not a per-session
  breakdown; confirm that estimate is acceptable before relying on it.
- ✅ Pricing is now a real, admin-editable table (`PricingRate` — see
  Django admin, `python manage.py seed_pricing` to create the initial
  rows) instead of a hardcoded constant. Rates (5€/10€/17€/35€ per hour by
  tier) were already confirmed with the product owner — only *where* they
  live has changed.
- ✅ Refunds are now wired up for both auto-cancelled undersubscribed
  sessions (always refunded — KLASSX's fault, not the student's) and
  student/admin-initiated cancellations within the notice period — see
  `core/services/payments.py: refund_enrollment_if_paid`.

## 5. Suggested next steps, in order

1. **Add real API keys** (Stripe, Daily.co, email) as described in section 3.
2. **Scheduled jobs** — ✅ done, see "Scheduled jobs" above — just wire up
   `run_scheduled_jobs` (or `deploy/crontab.example`) on your actual
   hosting platform, since that last step depends on where you deploy.
3. ✅ **Teacher payouts** — done, see "Open business-rule decisions" above
   (`compute_payouts` + the admin's "Marquer comme payé" action).
4. **Teacher/admin frontend polish** — the React pages exist and work
   end-to-end but haven't been visually refined to match the mockups as
   closely as the student-facing pages. (In progress.)

## 6. Before production

- ✅ Rate limiting on `/api/auth/login/`, `/api/auth/register/`,
  `/api/auth/register-teacher/` and `/api/auth/password-reset/` — done via
  DRF `ScopedRateThrottle` (see `LoginView`, `RegisterView`,
  `TeacherRegisterView`, `PasswordResetRequestView`,
  `PasswordResetConfirmView` in `core/views.py`, rates in
  `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` in `settings.py`).
- ✅ HTTPS/cookie hardening (SSL redirect, secure cookies, HSTS) — done,
  auto-enabled by `DJANGO_DEBUG=False`. HSTS itself is opt-in via its own
  `SECURE_HSTS_SECONDS` env var (defaults to 0) — see `.env.production.example`
  for the safe rollout order.
- ✅ Production WSGI server + static files — `gunicorn` and `whitenoise` are
  in `requirements.txt`, wired into `MIDDLEWARE`/`STORAGES` in
  `settings.py`, and a `Procfile` is included for Heroku/Railway-style
  platforms. Run `python manage.py collectstatic --noinput` once before
  starting (or as a build step) so whitenoise has files to serve; start
  the app with `gunicorn klassx.wsgi --log-file -` instead of
  `manage.py runserver`. Render doesn't read `Procfile` — set the build
  command to `pip install -r requirements.txt && python manage.py collectstatic --noinput`
  and the start command to `gunicorn klassx.wsgi` in its dashboard instead.
- ⬜ Set `DJANGO_DEBUG=False` and a real `DJANGO_SECRET_KEY` in your `.env`
  (see `.env.production.example`).
- ⬜ Switch to PostgreSQL (`DATABASE_NAME` etc. in `.env`).
- ⬜ Tighten `CORS_ALLOWED_ORIGINS` to your real frontend domain.
- ⬜ Configure real SMTP credentials (`EMAIL_HOST`, etc.) — without them,
  registration/password-reset emails silently never send.
- ⬜ Un premier socle de tests existe (`core/tests/`, voir section 1bis) —
  authentification/permissions, payouts, consentement parental, et
  désormais les réservations/paiements/planification de groupe. Encore
  non couverts : le handler webhook Stripe lui-même,
  `generate_series_occurrences`, et `cancel_undersubscribed_sessions`.
- ✅ Consentement parental (mineurs) — le compte d'un élève mineur est un
  compte UNIQUE et partagé : le formulaire d'inscription demande l'email
  et le mot de passe DU PARENT (qui deviennent les identifiants de
  connexion), ainsi que le nom du parent et celui de l'élève. C'est cette
  inscription conjointe — mot de passe choisi ensemble — qui vaut
  consentement : `ParentalConsent` est créé `CONFIRMED` immédiatement à
  l'inscription (IP capturée comme preuve), sans email à cliquer ni délai
  d'attente. Les 3 points qui déclenchent un vrai paiement
  (`PaymentMethodSetupView`, `EnrollmentViewSet.create_checkout_session`,
  `IndividualBookingView`) restent gardés par `requires_parental_consent`,
  mais celui-ci n'est plus jamais bloquant pour un compte créé de cette
  façon — il reste un filet de sécurité pour tout cas où le statut serait
  repassé manuellement à "pending" en base. Seuil choisi : 18 ans uniforme
  (voir le docstring de `ParentalConsent` dans `core/models.py` — le
  Québec, avec sa Loi 25, permettrait à un mineur de 14 ans et plus de
  consentir seul à la collecte de ses données, mais KLASSX garde un seuil
  unique pour ne pas gérer deux mécanismes différents selon la
  juridiction de l'élève).
  **Ceci reste un dispositif technique, pas un avis juridique** — à faire
  valider par un juriste dans chaque juridiction pertinente pour KLASSX
  (seuil d'âge, mécanisme de consentement par mot de passe conjoint,
  durée de conservation) avant de compter dessus en production. La
  politique de rétention des données au sens large reste, elle,
  entièrement à faire.
