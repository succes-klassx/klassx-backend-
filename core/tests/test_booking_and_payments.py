"""
Tests des réservations et paiements — la partie la plus complexe et la
moins couverte du projet jusqu'ici (voir README.md, section "Suggested
next steps"). Stripe est systématiquement mocké via unittest.mock.patch
sur les fonctions de core/services/payments.py : aucun de ces tests ne
fait un vrai appel réseau.

Couvre dans ce fichier :
- EnrollmentTests : capacité directe vs liste d'attente, promotion
  automatique de la liste d'attente, checkout Stripe (déjà payé/en liste
  d'attente rejetés), annulation avec/sans remboursement selon le délai.
- IndividualBookingTests : le flux "cours individuel" (paiement immédiat,
  pas de passage par GroupRequest).

Voir test_group_booking_flow.py pour le flux de groupe (GroupRequest ->
GroupAssignment -> schedule), plus complexe et testé séparément.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import ClassSession, Enrollment, Payment, StudentProfile, Subject

User = get_user_model()


def make_student(email="eleve@example.com", adult=True):
    user = User.objects.create_user(username=email, email=email, password="x", role=User.Role.STUDENT)
    dob = timezone.localdate().replace(year=timezone.localdate().year - (20 if adult else 16))
    StudentProfile.objects.create(user=user, bac_type="general", grade_level="terminale", date_of_birth=dob)
    return user


def make_subject(**overrides):
    defaults = dict(name="Français", code="fr", level=Subject.Level.BOTH,
                     bac_type="general", subject_type=Subject.SubjectType.COMMON_CORE)
    defaults.update(overrides)
    return Subject.objects.create(**defaults)


def make_session(subject, hours_from_now=48, duration_hours=1, group_tier=ClassSession.GroupTier.GROUP_3, max_capacity=3):
    start = timezone.now() + timedelta(hours=hours_from_now)
    return ClassSession.objects.create(
        subject=subject, level="terminale", group_tier=group_tier, max_capacity=max_capacity,
        start_time=start, end_time=start + timedelta(hours=duration_hours),
    )


class EnrollmentBookingTests(APITestCase):
    """perform_create : place directement si capacité, sinon liste d'attente."""

    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.session = make_session(self.subject, group_tier=ClassSession.GroupTier.GROUP_3, max_capacity=3)

    def test_booking_with_capacity_is_not_waitlisted(self):
        student = make_student("a@example.com")
        self.client.force_authenticate(user=student)
        url = reverse("enrollment-list")
        response = self.client.post(url, {"class_session": self.session.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertFalse(response.data["waitlisted"])

    def test_booking_full_session_is_waitlisted(self):
        # Remplit les 3 places avec des inscriptions déjà payées (seules les
        # inscriptions payées et non en liste d'attente comptent pour la
        # capacité — voir ClassSession.confirmed_seats_taken).
        for i in range(3):
            s = make_student(f"full{i}@example.com")
            Enrollment.objects.create(student=s, class_session=self.session, payment_status=Enrollment.PaymentStatus.PAID)

        latecomer = make_student("retardataire@example.com")
        self.client.force_authenticate(user=latecomer)
        url = reverse("enrollment-list")
        response = self.client.post(url, {"class_session": self.session.id}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertTrue(response.data["waitlisted"])

    def test_pending_unpaid_enrollments_do_not_count_toward_capacity(self):
        # 3 inscriptions PENDING (jamais payées) ne doivent pas bloquer une
        # 4e — seules les places PAYÉES comptent.
        for i in range(3):
            s = make_student(f"pending{i}@example.com")
            Enrollment.objects.create(student=s, class_session=self.session)  # payment_status=pending par défaut

        student = make_student("nouveau@example.com")
        self.client.force_authenticate(user=student)
        url = reverse("enrollment-list")
        response = self.client.post(url, {"class_session": self.session.id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertFalse(response.data["waitlisted"])


class EnrollmentCheckoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.session = make_session(self.subject)
        self.student = make_student("payeur@example.com")
        self.client.force_authenticate(user=self.student)

    @patch("core.views.payments.create_enrollment_checkout_session")
    def test_checkout_session_creates_payment_row(self, mock_checkout):
        mock_checkout.return_value = MagicMock(id="cs_test_123", url="https://stripe.test/pay")
        enrollment = Enrollment.objects.create(student=self.student, class_session=self.session)

        url = reverse("enrollment-create-checkout-session", args=[enrollment.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["checkout_url"], "https://stripe.test/pay")
        payment = Payment.objects.get(enrollment=enrollment)
        self.assertEqual(payment.stripe_checkout_session_id, "cs_test_123")
        mock_checkout.assert_called_once()

    def test_checkout_rejected_for_waitlisted_enrollment(self):
        enrollment = Enrollment.objects.create(student=self.student, class_session=self.session, waitlisted=True)
        url = reverse("enrollment-create-checkout-session", args=[enrollment.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_checkout_rejected_for_already_paid_enrollment(self):
        enrollment = Enrollment.objects.create(
            student=self.student, class_session=self.session, payment_status=Enrollment.PaymentStatus.PAID
        )
        url = reverse("enrollment-create-checkout-session", args=[enrollment.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("core.views.payments.create_enrollment_checkout_session")
    def test_checkout_returns_502_on_stripe_error(self, mock_checkout):
        mock_checkout.side_effect = Exception("réseau Stripe indisponible")
        enrollment = Enrollment.objects.create(student=self.student, class_session=self.session)
        url = reverse("enrollment-create-checkout-session", args=[enrollment.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertFalse(Payment.objects.filter(enrollment=enrollment).exists())  # pas de ligne orpheline


class EnrollmentCancelTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.student = make_student("annule@example.com")
        self.client.force_authenticate(user=self.student)

    def make_paid_enrollment(self, hours_from_now):
        session = make_session(self.subject, hours_from_now=hours_from_now)
        enrollment = Enrollment.objects.create(
            student=self.student, class_session=session, payment_status=Enrollment.PaymentStatus.PAID
        )
        Payment.objects.create(
            user=self.student, enrollment=enrollment, amount="25.00",
            status=Payment.Status.SUCCEEDED, stripe_payment_intent_id="pi_test_1",
        )
        return enrollment

    @patch("core.services.payments.refund_payment")
    def test_cancel_within_notice_period_refunds(self, mock_refund):
        # CANCELLATION_NOTICE_HOURS = 24 (core/views.py) — 48h avant, donc dans le délai.
        enrollment = self.make_paid_enrollment(hours_from_now=48)
        url = reverse("enrollment-cancel", args=[enrollment.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.cancellation_reason, "cancelled_within_notice_period")
        self.assertEqual(enrollment.payment_status, Enrollment.PaymentStatus.REFUNDED)
        mock_refund.assert_called_once()

        payment = Payment.objects.get(enrollment=enrollment)
        self.assertEqual(payment.status, Payment.Status.REFUNDED)

    @patch("core.services.payments.refund_payment")
    def test_late_cancellation_by_student_does_not_refund(self, mock_refund):
        # 12h avant, sous les 24h de préavis.
        enrollment = self.make_paid_enrollment(hours_from_now=12)
        url = reverse("enrollment-cancel", args=[enrollment.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.cancellation_reason, "late_cancellation_no_refund")
        self.assertEqual(enrollment.payment_status, Enrollment.PaymentStatus.PAID)  # inchangé, pas remboursé
        mock_refund.assert_not_called()

    @patch("core.services.payments.refund_payment")
    def test_admin_cancellation_always_refunds_even_late(self, mock_refund):
        admin = User.objects.create_user(username="admin1", email="admin1@example.com", password="x", role=User.Role.ADMIN)
        enrollment = self.make_paid_enrollment(hours_from_now=2)  # très tard
        self.client.force_authenticate(user=admin)

        url = reverse("enrollment-cancel", args=[enrollment.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.cancellation_reason, "cancelled_within_notice_period")
        mock_refund.assert_called_once()

    def test_cancelling_frees_seat_and_promotes_next_waitlisted(self):
        session = make_session(self.subject, group_tier=ClassSession.GroupTier.GROUP_3, max_capacity=3, hours_from_now=48)
        # Remplit la session à 3/3 payées.
        paying_enrollments = []
        for i in range(3):
            s = make_student(f"p{i}@example.com")
            e = Enrollment.objects.create(student=s, class_session=session, payment_status=Enrollment.PaymentStatus.PAID)
            paying_enrollments.append(e)

        waitlisted_student = make_student("attente@example.com")
        waitlisted = Enrollment.objects.create(student=waitlisted_student, class_session=session, waitlisted=True)

        to_cancel = paying_enrollments[0]
        self.client.force_authenticate(user=to_cancel.student)
        url = reverse("enrollment-cancel", args=[to_cancel.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        waitlisted.refresh_from_db()
        self.assertFalse(waitlisted.waitlisted)  # promu automatiquement

    def test_cannot_cancel_another_students_enrollment(self):
        enrollment = self.make_paid_enrollment(hours_from_now=48)
        other_student = make_student("autre@example.com")
        self.client.force_authenticate(user=other_student)

        url = reverse("enrollment-cancel", args=[enrollment.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # get_queryset filtre déjà par student


class IndividualBookingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.student = make_student("individuel@example.com")
        self.client.force_authenticate(user=self.student)

    def valid_payload(self, **overrides):
        start = timezone.now() + timedelta(days=2)
        payload = {
            "subject": self.subject.id,
            "level": "terminale",
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=1)).isoformat(),
        }
        payload.update(overrides)
        return payload

    @patch("core.views.payments.create_enrollment_checkout_session")
    def test_creates_session_enrollment_and_checkout(self, mock_checkout):
        mock_checkout.return_value = MagicMock(id="cs_indiv_1", url="https://stripe.test/pay-indiv")
        url = reverse("individual-booking")
        response = self.client.post(url, self.valid_payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["checkout_url"], "https://stripe.test/pay-indiv")

        session = ClassSession.objects.get(subject=self.subject, level="terminale")
        self.assertEqual(session.group_tier, ClassSession.GroupTier.INDIVIDUAL)
        self.assertEqual(session.max_capacity, 1)
        enrollment = Enrollment.objects.get(student=self.student, class_session=session)
        self.assertFalse(enrollment.waitlisted)
        Payment.objects.get(enrollment=enrollment, stripe_checkout_session_id="cs_indiv_1")

    def test_end_before_start_is_rejected(self):
        start = timezone.now() + timedelta(days=2)
        url = reverse("individual-booking")
        response = self.client.post(
            url,
            self.valid_payload(start_time=start.isoformat(), end_time=(start - timedelta(hours=1)).isoformat()),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duration_over_4_hours_is_rejected(self):
        start = timezone.now() + timedelta(days=2)
        url = reverse("individual-booking")
        response = self.client.post(
            url, self.valid_payload(end_time=(start + timedelta(hours=5)).isoformat()), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("core.views.payments.create_enrollment_checkout_session")
    def test_stripe_failure_returns_502_but_keeps_enrollment(self, mock_checkout):
        # Le comportement actuel de la vue : la session/l'enrollment sont
        # déjà créés en base avant l'appel Stripe et ne sont PAS annulés si
        # Stripe échoue (pas de rollback explicite ici) — ce test documente
        # ce comportement existant, pas une garantie que ce soit idéal.
        mock_checkout.side_effect = Exception("Stripe down")
        url = reverse("individual-booking")
        response = self.client.post(url, self.valid_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertTrue(Enrollment.objects.filter(student=self.student).exists())
