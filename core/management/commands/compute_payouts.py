"""
Computes each active teacher's Payout for a calendar month — spec 5.4.
This is the piece flagged in README.md section 4 as "not automated yet":
the Payout model already existed but nothing populated it.

Run monthly, once the month being paid is fully over — the 1st is the
natural choice:
    0 7 1 * * cd /path/to/klassx_backend && venv/bin/python manage.py compute_payouts

Usage:
    python manage.py compute_payouts                  # previous calendar month
    python manage.py compute_payouts --month 2026-07   # a specific month

Deliberately NOT added to `run_scheduled_jobs`'s every-5-minutes wrapper —
unlike the other jobs there, this one only makes sense monthly, and
recomputing it constantly would just waste queries. Add the line above to
your crontab, or `deploy/crontab.example`, on its own.

Idempotent, but not silently repeatable forever like the other jobs: a
Payout already marked PAID for a given teacher+period+currency is left
untouched (money's already gone out — it shouldn't change under an
admin's feet). A still-PENDING Payout for the same teacher+period+currency
is *updated* in place if you re-run this after a late cancellation/refund
changes the numbers.

What counts as "a session that happened" mirrors AdminTeacherHoursView's
convention (see core/views.py: AdminTeacherHoursView docstring) —
assigned, not cancelled, start_time in the past. ClassSession.Status.COMPLETED
exists in the model but nothing sets it yet; swap the start_time__lte=now
filter below for status=COMPLETED if you add real completion-tracking later.

CURRENCY SPLIT — a teacher is paid in TND for the sessions where the
paying student is in Tunisia, and in EUR for everyone else. NEVER a
single combined figure: Payout has one row per (teacher, period,
currency) — see the Payout model docstring in core/models.py. This
mirrors the same routing already used at checkout time (Stripe/EUR vs
Konnect/TND — see EnrollmentViewSet.create_checkout_session), and is
based on the paying student's `country`, not a currency conversion.
Applies to ALL three compensation_type values below, not just
"percentage" — a flat-per-session or per-student teacher who taught a
mixed EUR/TND group session gets that one session's flat fee (or that
session's per-student count) split proportionally between the two
currencies, based on which currency each of that session's paying
students is in.

How revenue is worked out for compensation_type="percentage":
- One-off / INDIVIDUAL sessions, or any session with at least one real
  succeeded Payment tied to it: the real Payment rows tied to the
  session's enrollments (each student pays session_price_cents, or its
  TND equivalent for Tunisia, individually — see
  EnrollmentViewSet.create_checkout_session), grouped by their own
  `currency` field — exact, no estimation needed.
- Recurring group sessions with NO real Payment row tied to them at all
  (billed monthly through a SeriesMembership's Stripe Subscription or a
  Konnect one-off — see core/services/payments.py:
  charge_saved_payment_method / EnrollmentViewSet.checkout): there's no
  per-session Payment row for these, since one monthly charge covers
  several weekly occurrences at once. This command ESTIMATES each such
  session's revenue as session_price_cents() (or session_price_millimes_tnd()
  for Tunisia) x (paid, non-waitlisted enrollments on it, split by each
  student's own country) — the same per-student rate the platform
  actually charges, just reconstructed session-by-session since neither
  gateway hands us that breakdown. Confirm this estimate is acceptable
  before relying on it for real payouts — see README.md "Open
  business-rule decisions".
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import ClassSession, Enrollment, Payment, Payout, TeacherProfile
from core.pricing import session_price_cents, session_price_millimes_tnd


class Command(BaseCommand):
    help = "Computes/updates each active teacher's Payout(s) for a calendar month (defaults to the previous month) — one row per currency."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month", type=str, default=None,
            help="Month to compute, format YYYY-MM (defaults to the previous calendar month).",
        )

    def handle(self, *args, **options):
        period_start, period_end = self._resolve_period(options["month"])
        now = timezone.now()

        base_qs = (
            ClassSession.objects.filter(
                assigned_teacher__isnull=False,
                start_time__gte=period_start,
                start_time__lt=period_end,
                start_time__lte=now,
            )
            .exclude(status=ClassSession.Status.CANCELLED)
        )

        created, updated, skipped_paid, skipped_zero = 0, 0, 0, 0

        for teacher in TeacherProfile.objects.filter(is_active=True):
            sessions = list(base_qs.filter(assigned_teacher=teacher))
            if not sessions:
                continue

            amounts_by_currency = self._compensation_for(teacher, sessions)
            if not amounts_by_currency:
                skipped_zero += 1
                continue

            period_end_date = (period_end - timedelta(days=1)).date()

            for currency, amount in amounts_by_currency.items():
                if amount <= 0:
                    continue
                existing = Payout.objects.filter(
                    teacher=teacher, period_start=period_start.date(),
                    period_end=period_end_date, currency=currency,
                ).first()

                if existing and existing.status == Payout.Status.PAID:
                    skipped_paid += 1
                    continue

                if existing:
                    existing.amount = amount
                    existing.save(update_fields=["amount"])
                    updated += 1
                else:
                    Payout.objects.create(
                        teacher=teacher, period_start=period_start.date(),
                        period_end=period_end_date, amount=amount, currency=currency,
                    )
                    created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Payouts for {period_start:%Y-%m}: {created} created, {updated} updated, "
            f"{skipped_paid} already paid (untouched), {skipped_zero} teachers skipped (nothing owed)."
        ))

    # ------------------------------------------------------------------
    def _resolve_period(self, month_str):
        """Returns (period_start, period_end) as tz-aware datetimes, period_end exclusive."""
        now = timezone.localtime()
        if month_str:
            try:
                month_start = datetime.strptime(month_str, "%Y-%m").replace(tzinfo=now.tzinfo)
            except ValueError:
                raise CommandError("--month must be in YYYY-MM format, e.g. 2026-07.")
        else:
            first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)

        month_end = (
            month_start.replace(year=month_start.year + 1, month=1)
            if month_start.month == 12
            else month_start.replace(month=month_start.month + 1)
        )
        return month_start, month_end

    # ------------------------------------------------------------------
    @staticmethod
    def _currency_for_student(student):
        """Same signal used to route a student's own checkout to Stripe vs Konnect — see EnrollmentViewSet.create_checkout_session."""
        return "TND" if student.country == "Tunisie" else "EUR"

    def _session_currency_counts(self, session):
        """{currency: count} of this session's paid, non-waitlisted enrollments, split by each paying student's own country."""
        counts = {}
        for enrollment in session.enrollments.filter(
            payment_status=Enrollment.PaymentStatus.PAID, waitlisted=False
        ).select_related("student"):
            currency = self._currency_for_student(enrollment.student)
            counts[currency] = counts.get(currency, 0) + 1
        return counts

    def _compensation_for(self, teacher, sessions):
        """
        {currency: Decimal amount} owed for this teacher's sessions, per
        their compensation_type — see module docstring. NEVER a single
        combined Decimal across currencies: a teacher with both EUR- and
        TND-paying students gets one entry per currency here, which
        becomes one Payout row each (see Payout model).
        """
        if teacher.compensation_type == "flat_per_session":
            return self._flat_per_session(teacher, sessions)
        if teacher.compensation_type == "per_student":
            return self._per_student(teacher, sessions)
        if teacher.compensation_type == "percentage":
            return self._percentage(teacher, sessions)
        return {}

    def _flat_per_session(self, teacher, sessions):
        """Flat rate per session, prorated across currencies by each session's mix of paying students (a session with 7 EUR students and 3 TND students splits that session's flat fee 70/30)."""
        totals = {}
        for session in sessions:
            counts = self._session_currency_counts(session)
            total_seats = sum(counts.values())
            if total_seats == 0:
                # No payment info to split by (e.g. all still pending) —
                # the session still happened, so it's still owed; default
                # to EUR, the platform's base currency, for lack of any
                # other signal.
                totals["EUR"] = totals.get("EUR", Decimal("0")) + teacher.compensation_rate
                continue
            for currency, n in counts.items():
                totals[currency] = totals.get(currency, Decimal("0")) + teacher.compensation_rate * Decimal(n) / Decimal(total_seats)
        return {c: round(v, 2) for c, v in totals.items() if v > 0}

    def _per_student(self, teacher, sessions):
        """Rate x confirmed seats, split exactly by each seat's own currency."""
        totals = {}
        for session in sessions:
            for currency, n in self._session_currency_counts(session).items():
                totals[currency] = totals.get(currency, Decimal("0")) + teacher.compensation_rate * Decimal(n)
        return {c: round(v, 2) for c, v in totals.items() if v > 0}

    def _percentage(self, teacher, sessions):
        """Revenue-share, split exactly by the real Payment.currency where one exists, or estimated per-student by country otherwise — see module docstring."""
        revenue = {}
        for session in sessions:
            real_payments = list(
                Payment.objects.filter(enrollment__class_session=session, status=Payment.Status.SUCCEEDED)
            )
            if real_payments:
                for payment in real_payments:
                    revenue[payment.currency] = revenue.get(payment.currency, Decimal("0")) + payment.amount
            else:
                # No real Payment row for this session — it was billed
                # through a monthly Subscription/recurring charge instead.
                # See docstring.
                paid_enrollments = session.enrollments.filter(
                    payment_status=Enrollment.PaymentStatus.PAID, waitlisted=False,
                ).select_related("student")
                for enrollment in paid_enrollments:
                    if self._currency_for_student(enrollment.student) == "TND":
                        revenue["TND"] = revenue.get("TND", Decimal("0")) + Decimal(session_price_millimes_tnd(session)) / 1000
                    else:
                        revenue["EUR"] = revenue.get("EUR", Decimal("0")) + Decimal(session_price_cents(session)) / 100

        rate_fraction = teacher.compensation_rate / Decimal(100)
        return {c: round(v * rate_fraction, 2) for c, v in revenue.items() if v > 0}

