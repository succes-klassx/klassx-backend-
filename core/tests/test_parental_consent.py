"""
Tests du consentement parental (RGPD/mineurs) — voir ParentalConsent et
StudentProfile.date_of_birth (core/models.py) pour le contexte complet.

Le compte d'un élève mineur est un compte UNIQUE et partagé : le
formulaire d'inscription demande l'email et le mot de passe DU PARENT
(qui deviennent les identifiants de connexion), ainsi que le nom du
parent et celui de l'élève. C'est cette inscription conjointe qui vaut
consentement — le statut passe à CONFIRMED immédiatement, il n'y a plus
de lien par email à cliquer ni d'attente.

Suit le même style que test_auth.py : cache.clear() en setUp, et des
assertions sur mail.outbox plutôt que sur un vrai envoi SMTP.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import ClassSession, Enrollment, ParentalConsent, StudentProfile, Subject

User = get_user_model()


def minor_dob(years_old=16):
    today = date.today()
    return today.replace(year=today.year - years_old)


def adult_dob(years_old=20):
    today = date.today()
    return today.replace(year=today.year - years_old)


class StudentRegistrationParentalConsentTests(APITestCase):
    def setUp(self):
        cache.clear()

    def base_payload(self, **overrides):
        payload = {
            # Pour un mineur, cet email EST celui du parent — voir
            # docstring du module et ParentalConsent (core/models.py).
            "email": "parent@example.com",
            "username": "compteparent1",
            "password": "un-mot-de-passe-solide-42",
            "first_name": "Léa",  # nom de l'ÉLÈVE
            "last_name": "Martin",
            "bac_type": "general",
            "grade_level": "terminale",
            "date_of_birth": minor_dob(16).isoformat(),
        }
        payload.update(overrides)
        return payload

    def test_minor_registration_requires_parent_full_name(self):
        url = reverse("register")
        response = self.client.post(url, self.base_payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent_full_name", response.data)

    def test_minor_registration_creates_confirmed_consent_immediately(self):
        url = reverse("register")
        payload = self.base_payload(parent_full_name="Sophie Martin")
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        profile = StudentProfile.objects.get(user__email="parent@example.com")
        self.assertTrue(profile.is_minor)
        # Confirmé IMMÉDIATEMENT — pas d'attente, pas de lien à cliquer.
        self.assertFalse(profile.requires_parental_consent)

        consent = ParentalConsent.objects.get(student=profile)
        self.assertEqual(consent.status, ParentalConsent.Status.CONFIRMED)
        self.assertIsNotNone(consent.confirmed_at)
        self.assertEqual(consent.parent_full_name, "Sophie Martin")
        self.assertEqual(consent.parent_email, "parent@example.com")  # même adresse que le login

        # Un seul email, envoyé à l'adresse du compte (celle du parent).
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {"parent@example.com"})

    def test_adult_registration_does_not_require_parent_full_name(self):
        url = reverse("register")
        payload = self.base_payload(email="eleve-majeur@example.com", date_of_birth=adult_dob(20).isoformat())
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        profile = StudentProfile.objects.get(user__email="eleve-majeur@example.com")
        self.assertFalse(profile.is_minor)
        self.assertFalse(profile.requires_parental_consent)
        self.assertFalse(ParentalConsent.objects.filter(student=profile).exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_minor_registration_records_request_ip_as_consent_evidence(self):
        url = reverse("register")
        payload = self.base_payload(parent_full_name="Sophie Martin")
        response = self.client.post(url, payload, format="json", REMOTE_ADDR="203.0.113.42")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        consent = ParentalConsent.objects.get(student__user__email="parent@example.com")
        self.assertEqual(consent.confirmed_ip, "203.0.113.42")


class ParentalConsentGatingTests(APITestCase):
    """
    Vérifie que les 3 points qui déclenchent un vrai paiement fonctionnent
    normalement pour un compte mineur créé via l'inscription conjointe —
    le consentement étant confirmé dès l'inscription, il n'y a plus de
    fenêtre bloquée entre la création du compte et le premier paiement.
    """
    def setUp(self):
        cache.clear()
        url = reverse("register")
        payload = {
            "email": "familledupont@example.com", "username": "familledupont", "password": "mot-de-passe-42",
            "first_name": "Noah", "last_name": "Dupont", "bac_type": "general", "grade_level": "terminale",
            "date_of_birth": minor_dob(16).isoformat(), "parent_full_name": "Claire Dupont",
        }
        self.client.post(url, payload, format="json")
        self.user = User.objects.get(email="familledupont@example.com")
        self.profile = self.user.student_profile
        self.client.force_authenticate(user=self.user)

    def test_payment_method_setup_is_not_blocked_right_after_registration(self):
        from unittest.mock import patch, MagicMock
        with patch("core.views.payments.create_card_setup_checkout_session") as mock_checkout:
            mock_checkout.return_value = MagicMock(url="https://stripe.test/setup")
            url = reverse("payment-method-setup")
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_checkout.assert_called_once()

    def test_individual_booking_is_not_blocked_right_after_registration(self):
        from datetime import timedelta
        from django.utils import timezone
        from unittest.mock import patch, MagicMock

        subject = Subject.objects.create(name="Français", code="fr")
        start = timezone.now() + timedelta(days=2)
        with patch("core.views.payments.create_enrollment_checkout_session") as mock_checkout:
            mock_checkout.return_value = MagicMock(id="cs_1", url="https://stripe.test/pay")
            url = reverse("individual-booking")
            response = self.client.post(
                url,
                {
                    "subject": subject.id, "level": "terminale",
                    "start_time": start.isoformat(), "end_time": (start + timedelta(hours=1)).isoformat(),
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_enrollment_checkout_is_not_blocked_right_after_registration(self):
        from datetime import timedelta
        from django.utils import timezone
        from unittest.mock import patch, MagicMock

        subject = Subject.objects.create(name="Français", code="fr")
        session = ClassSession.objects.create(
            subject=subject, level="terminale", group_tier=ClassSession.GroupTier.INDIVIDUAL, max_capacity=1,
            start_time=timezone.now() + timedelta(days=1), end_time=timezone.now() + timedelta(days=1, hours=1),
        )
        enrollment = Enrollment.objects.create(student=self.user, class_session=session)

        with patch("core.views.payments.create_enrollment_checkout_session") as mock_checkout:
            mock_checkout.return_value = MagicMock(id="cs_2", url="https://stripe.test/pay2")
            url = reverse("enrollment-create-checkout-session", args=[enrollment.id])
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_still_blocked_if_admin_manually_resets_consent_to_pending(self):
        # Cas limite : un admin qui réinitialise manuellement le statut en
        # base (édge case, pas un flux normal) doit quand même bloquer les
        # paiements — la propriété requires_parental_consent doit rester
        # fiable, pas seulement "vraie au moment de l'inscription".
        consent = self.profile.parental_consent
        consent.status = ParentalConsent.Status.PENDING
        consent.save(update_fields=["status"])

        url = reverse("payment-method-setup")
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "parental_consent_required")
