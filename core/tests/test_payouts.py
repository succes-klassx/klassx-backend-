"""
Tests de `python manage.py compute_payouts` — voir le docstring de la
commande (core/management/commands/compute_payouts.py) pour le détail des
règles de calcul par type de rémunération.

Chaque test passe explicitement --month avec le mois EN COURS, plutôt que
de compter sur le comportement par défaut de la commande (mois précédent,
pensé pour un vrai cron le 1er du mois) — sans ça, une séance créée
"il y a 48h" tomberait parfois dans le mauvais mois selon le jour du mois
où les tests tournent.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import ClassSession, Enrollment, Payment, Payout, Subject, TeacherProfile

User = get_user_model()


def make_teacher(email, compensation_type, compensation_rate):
    user = User.objects.create_user(username=email, email=email, password="x", role=User.Role.TEACHER)
    return TeacherProfile.objects.create(
        user=user, is_active=True,
        compensation_type=compensation_type, compensation_rate=compensation_rate,
    )


def make_student(email, country=""):
    return User.objects.create_user(username=email, email=email, password="x", role=User.Role.STUDENT, country=country)


class ComputePayoutsTests(TestCase):
    def setUp(self):
        self.subject = Subject.objects.create(name="Français", code="fr")
        now = timezone.now()
        self.current_month = now.strftime("%Y-%m")
        # Bornes exactes du mois en cours, telles que la commande les calcule
        # elle-même — utilisées pour construire un Payout "déjà existant"
        # dans les tests qui vérifient la non-régression / l'idempotence.
        self.period_start = date(now.year, now.month, 1)
        self.period_end = date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])

    def make_past_session(self, teacher, hours_ago=48, duration_hours=1, group_tier=ClassSession.GroupTier.INDIVIDUAL,
                           max_capacity=1, status_=ClassSession.Status.ASSIGNED):
        start = timezone.now() - timedelta(hours=hours_ago)
        return ClassSession.objects.create(
            subject=self.subject, level="terminale", group_tier=group_tier, max_capacity=max_capacity,
            assigned_teacher=teacher, start_time=start, end_time=start + timedelta(hours=duration_hours),
            status=status_,
        )

    def run_command(self, month=None):
        out = StringIO()
        call_command("compute_payouts", "--month", month or self.current_month, stdout=out)
        return out.getvalue()

    def test_flat_per_session(self):
        teacher = make_teacher("flat@example.com", "flat_per_session", Decimal("20.00"))
        self.make_past_session(teacher, hours_ago=48)
        self.make_past_session(teacher, hours_ago=72)

        self.run_command()

        payout = Payout.objects.get(teacher=teacher)
        self.assertEqual(payout.amount, Decimal("40.00"))
        self.assertEqual(payout.status, Payout.Status.PENDING)

    def test_per_student_counts_paid_non_waitlisted_enrollments_only(self):
        teacher = make_teacher("perstudent@example.com", "per_student", Decimal("5.00"))
        session = self.make_past_session(teacher, hours_ago=48, group_tier=ClassSession.GroupTier.GROUP_4, max_capacity=4)

        paid = make_student("paid@example.com")
        waitlisted = make_student("waitlisted@example.com")
        unpaid = make_student("unpaid@example.com")
        Enrollment.objects.create(student=paid, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)
        Enrollment.objects.create(student=waitlisted, class_session=session,
                                   payment_status=Enrollment.PaymentStatus.PAID, waitlisted=True)
        Enrollment.objects.create(student=unpaid, class_session=session, payment_status=Enrollment.PaymentStatus.PENDING)

        self.run_command()

        payout = Payout.objects.get(teacher=teacher)
        self.assertEqual(payout.amount, Decimal("5.00"))  # un seul élève compte : payé et non liste d'attente

    def test_percentage_uses_real_payment_when_present(self):
        teacher = make_teacher("pct@example.com", "percentage", Decimal("60"))  # 60% pour le prof
        session = self.make_past_session(teacher, hours_ago=48)
        student = make_student("eleve@example.com")
        enrollment = Enrollment.objects.create(student=student, class_session=session,
                                                payment_status=Enrollment.PaymentStatus.PAID)
        Payment.objects.create(user=student, enrollment=enrollment, amount=Decimal("55.00"),
                                status=Payment.Status.SUCCEEDED)

        self.run_command()

        payout = Payout.objects.get(teacher=teacher)
        self.assertEqual(payout.amount, Decimal("33.00"))  # 60% de 55€

    def test_percentage_estimates_revenue_for_subscription_billed_session(self):
        # Séance de groupe récurrent : payée via abonnement Stripe mensuel,
        # donc aucune ligne Payment — l'estimation doit prendre le relais.
        teacher = make_teacher("pct2@example.com", "percentage", Decimal("50"))
        session = self.make_past_session(teacher, hours_ago=48, group_tier=ClassSession.GroupTier.GROUP_4,
                                          max_capacity=4, duration_hours=1)
        student = make_student("abonne@example.com")
        Enrollment.objects.create(student=student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)

        self.run_command()

        payout = Payout.objects.get(teacher=teacher)
        self.assertGreater(payout.amount, Decimal("0"))

    def test_future_and_cancelled_sessions_are_ignored(self):
        teacher = make_teacher("ignore@example.com", "flat_per_session", Decimal("20.00"))
        now = timezone.now()
        ClassSession.objects.create(
            subject=self.subject, level="terminale", group_tier=ClassSession.GroupTier.INDIVIDUAL, max_capacity=1,
            assigned_teacher=teacher, start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=1),
        )
        self.make_past_session(teacher, hours_ago=10, status_=ClassSession.Status.CANCELLED)

        self.run_command()

        self.assertFalse(Payout.objects.filter(teacher=teacher).exists())

    def test_paid_payout_is_never_overwritten(self):
        teacher = make_teacher("paid@example.com", "flat_per_session", Decimal("20.00"))
        self.make_past_session(teacher, hours_ago=48)
        existing = Payout.objects.create(
            teacher=teacher, period_start=self.period_start, period_end=self.period_end, amount=Decimal("999.00"),
            status=Payout.Status.PAID,
        )

        self.run_command()

        existing.refresh_from_db()
        self.assertEqual(existing.amount, Decimal("999.00"))  # inchangé
        self.assertEqual(Payout.objects.filter(teacher=teacher).count(), 1)  # pas de doublon

    def test_pending_payout_is_recomputed_on_rerun(self):
        teacher = make_teacher("rerun@example.com", "flat_per_session", Decimal("20.00"))
        self.make_past_session(teacher, hours_ago=48)
        self.run_command()
        self.make_past_session(teacher, hours_ago=50)  # une deuxième séance avant le second passage

        self.run_command()

        payout = Payout.objects.get(teacher=teacher)
        self.assertEqual(payout.amount, Decimal("40.00"))
        self.assertEqual(Payout.objects.filter(teacher=teacher).count(), 1)

    def test_invalid_month_raises(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.run_command(month="pas-un-mois")

    # ------------------------------------------------------------------
    # Tunisie / Konnect — un enseignant avec des élèves EUR et des élèves
    # tunisiens (TND) doit recevoir DEUX Payout distincts, jamais un seul
    # montant mélangé. Voir le docstring du module (compute_payouts.py).
    def test_per_student_splits_eur_and_tnd_into_separate_payouts(self):
        teacher = make_teacher("mixed@example.com", "per_student", Decimal("5.00"))
        session = self.make_past_session(teacher, hours_ago=48, group_tier=ClassSession.GroupTier.GROUP_4, max_capacity=4)

        eur_student = make_student("eur@example.com")
        tnd_student = make_student("tunisien@example.com", country="Tunisie")
        Enrollment.objects.create(student=eur_student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)
        Enrollment.objects.create(student=tnd_student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)

        self.run_command()

        payouts = {p.currency: p.amount for p in Payout.objects.filter(teacher=teacher)}
        self.assertEqual(payouts, {"EUR": Decimal("5.00"), "TND": Decimal("5.00")})

    def test_flat_per_session_prorates_by_currency_mix(self):
        teacher = make_teacher("flatmixed@example.com", "flat_per_session", Decimal("20.00"))
        session = self.make_past_session(teacher, hours_ago=48, group_tier=ClassSession.GroupTier.GROUP_4, max_capacity=4)

        # 3 élèves EUR, 1 élève tunisien -> 15€ + 5€ équivalent en TND
        for i in range(3):
            s = make_student(f"eur{i}@example.com")
            Enrollment.objects.create(student=s, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)
        tnd_student = make_student("tn@example.com", country="Tunisie")
        Enrollment.objects.create(student=tnd_student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)

        self.run_command()

        payouts = {p.currency: p.amount for p in Payout.objects.filter(teacher=teacher)}
        self.assertEqual(payouts["EUR"], Decimal("15.00"))
        self.assertEqual(payouts["TND"], Decimal("5.00"))

    def test_percentage_splits_real_payments_by_their_own_currency(self):
        teacher = make_teacher("pcttnd@example.com", "percentage", Decimal("60"))
        session = self.make_past_session(teacher, hours_ago=48)
        eur_student = make_student("eurpay@example.com")
        tnd_student = make_student("tndpay@example.com", country="Tunisie")
        eur_enrollment = Enrollment.objects.create(student=eur_student, class_session=session,
                                                     payment_status=Enrollment.PaymentStatus.PAID)
        tnd_enrollment = Enrollment.objects.create(student=tnd_student, class_session=session,
                                                     payment_status=Enrollment.PaymentStatus.PAID)
        Payment.objects.create(user=eur_student, enrollment=eur_enrollment, amount=Decimal("55.00"),
                                currency="EUR", status=Payment.Status.SUCCEEDED)
        Payment.objects.create(user=tnd_student, enrollment=tnd_enrollment, amount=Decimal("80.00"),
                                currency="TND", status=Payment.Status.SUCCEEDED)

        self.run_command()

        payouts = {p.currency: p.amount for p in Payout.objects.filter(teacher=teacher)}
        self.assertEqual(payouts["EUR"], Decimal("33.00"))  # 60% de 55€
        self.assertEqual(payouts["TND"], Decimal("48.00"))  # 60% de 80 DT

    def test_paid_tnd_payout_untouched_while_eur_payout_still_updates(self):
        """Un Payout TND déjà payé ne doit pas bloquer le recalcul du Payout EUR de la même période — ce sont deux lignes indépendantes."""
        teacher = make_teacher("indep@example.com", "per_student", Decimal("5.00"))
        session = self.make_past_session(teacher, hours_ago=48, group_tier=ClassSession.GroupTier.GROUP_4, max_capacity=4)
        eur_student = make_student("eurindep@example.com")
        tnd_student = make_student("tnindep@example.com", country="Tunisie")
        Enrollment.objects.create(student=eur_student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)
        Enrollment.objects.create(student=tnd_student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)

        Payout.objects.create(
            teacher=teacher, period_start=self.period_start, period_end=self.period_end,
            amount=Decimal("999.00"), currency="TND", status=Payout.Status.PAID,
        )

        self.run_command()

        tnd_payout = Payout.objects.get(teacher=teacher, currency="TND")
        eur_payout = Payout.objects.get(teacher=teacher, currency="EUR")
        self.assertEqual(tnd_payout.amount, Decimal("999.00"))  # inchangé, déjà payé
        self.assertEqual(eur_payout.amount, Decimal("5.00"))  # recalculé normalement
