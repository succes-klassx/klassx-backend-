"""
Vérifie que le programme de parrainage (10% — core/services/referrals.py)
ne mélange jamais des commissions EUR et TND ensemble : un parrain avec un
filleul payant en euros ET un filleul tunisien payant en dinars (Konnect)
doit toujours voir deux totaux séparés, jamais une seule somme incorrecte.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import Payment, ReferralCommission
from core.services.referrals import create_referral_commission_if_applicable

User = get_user_model()


def make_user(email, role=User.Role.STUDENT, country="", referred_by=None):
    return User.objects.create_user(
        username=email, email=email, password="x", role=role, country=country, referred_by=referred_by,
    )


class ReferralCurrencySeparationTests(TestCase):
    def setUp(self):
        self.referrer = make_user("referrer@example.com")
        self.eur_filleul = make_user("filleul-eur@example.com", referred_by=self.referrer)
        self.tnd_filleul = make_user("filleul-tn@example.com", country="Tunisie", referred_by=self.referrer)

    def test_commission_currency_matches_payment_currency(self):
        eur_payment = Payment.objects.create(
            user=self.eur_filleul, amount=Decimal("50.00"), currency="EUR", status=Payment.Status.SUCCEEDED,
        )
        tnd_payment = Payment.objects.create(
            user=self.tnd_filleul, amount=Decimal("80.00"), currency="TND",
            gateway=Payment.Gateway.KONNECT, status=Payment.Status.SUCCEEDED,
        )

        eur_commission = create_referral_commission_if_applicable(eur_payment)
        tnd_commission = create_referral_commission_if_applicable(tnd_payment)

        self.assertEqual(eur_commission.currency, "EUR")
        self.assertEqual(eur_commission.amount, Decimal("5.00"))
        self.assertEqual(tnd_commission.currency, "TND")
        self.assertEqual(tnd_commission.amount, Decimal("8.00"))

    def test_admin_referrals_endpoint_never_mixes_currencies(self):
        eur_payment = Payment.objects.create(
            user=self.eur_filleul, amount=Decimal("50.00"), currency="EUR", status=Payment.Status.SUCCEEDED,
        )
        tnd_payment = Payment.objects.create(
            user=self.tnd_filleul, amount=Decimal("80.00"), currency="TND",
            gateway=Payment.Gateway.KONNECT, status=Payment.Status.SUCCEEDED,
        )
        create_referral_commission_if_applicable(eur_payment)
        create_referral_commission_if_applicable(tnd_payment)

        admin = make_user("admin@example.com", role=User.Role.ADMIN)
        client = APIClient()
        client.force_authenticate(admin)
        response = client.get(reverse("admin-referrals"))

        self.assertEqual(response.status_code, 200)
        rows = {row["currency"]: row for row in response.data["referrers"]}
        self.assertEqual(rows["EUR"]["total_earned"], "5.00")
        self.assertEqual(rows["TND"]["total_earned"], "8.00")
        # Jamais une seule ligne fusionnée à 13.00 (5€ + 8 DT n'a aucun sens) :
        self.assertEqual(len(rows), 2)

    def test_mark_paid_only_affects_the_given_currency(self):
        eur_payment = Payment.objects.create(
            user=self.eur_filleul, amount=Decimal("50.00"), currency="EUR", status=Payment.Status.SUCCEEDED,
        )
        tnd_payment = Payment.objects.create(
            user=self.tnd_filleul, amount=Decimal("80.00"), currency="TND",
            gateway=Payment.Gateway.KONNECT, status=Payment.Status.SUCCEEDED,
        )
        eur_commission = create_referral_commission_if_applicable(eur_payment)
        tnd_commission = create_referral_commission_if_applicable(tnd_payment)

        admin = make_user("admin2@example.com", role=User.Role.ADMIN)
        client = APIClient()
        client.force_authenticate(admin)
        response = client.post(
            reverse("admin-referrals-mark-paid", args=[self.referrer.id]) + "?currency=EUR"
        )

        self.assertEqual(response.status_code, 200)
        eur_commission.refresh_from_db()
        tnd_commission.refresh_from_db()
        self.assertTrue(eur_commission.paid_out)
        self.assertFalse(tnd_commission.paid_out)  # le virement TND est séparé, pas encore fait

    def test_user_referral_earnings_total_is_grouped_by_currency(self):
        eur_payment = Payment.objects.create(
            user=self.eur_filleul, amount=Decimal("50.00"), currency="EUR", status=Payment.Status.SUCCEEDED,
        )
        tnd_payment = Payment.objects.create(
            user=self.tnd_filleul, amount=Decimal("80.00"), currency="TND",
            gateway=Payment.Gateway.KONNECT, status=Payment.Status.SUCCEEDED,
        )
        create_referral_commission_if_applicable(eur_payment)
        create_referral_commission_if_applicable(tnd_payment)

        client = APIClient()
        client.force_authenticate(self.referrer)
        response = client.get(reverse("me"))

        self.assertEqual(response.data["referral_earnings_total"], {"EUR": "5.00", "TND": "8.00"})

    def test_no_referrer_means_no_commission(self):
        lone_student = make_user("lone@example.com")
        payment = Payment.objects.create(
            user=lone_student, amount=Decimal("50.00"), currency="EUR", status=Payment.Status.SUCCEEDED,
        )
        self.assertIsNone(create_referral_commission_if_applicable(payment))
        self.assertEqual(ReferralCommission.objects.count(), 0)
