"""
Tests des flux d'authentification (spec 3.A / 3.C) : inscription élève,
inscription enseignant, connexion, mot de passe oublié.

Ces flux sont les plus exposés (accessibles sans compte, donc les plus
ciblés par du brute-force/spam) et ceux où un bug est le plus coûteux :
un email de confirmation ou de reset qui ne part pas, un compte enseignant
activé par erreur, etc. — exactement les points qu'on a corrigés ensemble,
d'où leur priorité ici.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import StudentProfile, Subject, TeacherProfile

User = get_user_model()


class StudentRegistrationTests(APITestCase):
    def setUp(self):
        # Le throttling (ScopedRateThrottle) compte via le cache Django, qui
        # n'est PAS réinitialisé entre les tests comme l'est la base de
        # données — sans ce clear(), l'ordre d'exécution des tests pourrait
        # faire échouer un test à cause du quota consommé par un autre.
        cache.clear()

    def test_register_creates_user_and_student_profile(self):
        url = reverse("register")
        payload = {
            "email": "eleve@example.com",
            "username": "eleve1",
            "password": "un-mot-de-passe-solide-42",
            "first_name": "Léa",
            "last_name": "Martin",
            "bac_type": "general",
            "grade_level": "terminale",
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        user = User.objects.get(email="eleve@example.com")
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertTrue(StudentProfile.objects.filter(user=user).exists())

    def test_register_sends_confirmation_email(self):
        """
        Couvre directement le bug initial : aucun email de confirmation ne
        partait. En test, EMAIL_BACKEND bascule automatiquement sur
        locmem — on vérifie donc qu'un email est bien mis en file, pas
        qu'il atteint une vraie boîte (ça, c'est la config SMTP en prod,
        hors de portée d'un test automatisé).
        """
        url = reverse("register")
        payload = {
            "email": "confirmation@example.com",
            "username": "confirmuser",
            "password": "un-mot-de-passe-solide-42",
            "bac_type": "general",
            "grade_level": "1ere",
        }
        self.client.post(url, payload, format="json")

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("confirmation@example.com", mail.outbox[0].to)

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user(username="existant", email="deja@example.com", password="x")
        url = reverse("register")
        payload = {
            "email": "deja@example.com",
            "username": "nouveaucompte",
            "password": "un-mot-de-passe-solide-42",
            "bac_type": "general",
            "grade_level": "terminale",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_register_rejects_weak_password(self):
        url = reverse("register")
        payload = {
            "email": "faible@example.com",
            "username": "faibleuser",
            "password": "1234",
            "bac_type": "general",
            "grade_level": "terminale",
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TeacherRegistrationTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_register_teacher_stays_inactive_until_admin_approval(self):
        """
        Le point le plus sensible du flux enseignant (spec 3.C) : un
        compte fraîchement inscrit ne doit PAS pouvoir enseigner avant
        validation admin. Si ce test casse, un enseignant non vérifié
        pourrait se faire assigner des élèves.
        """
        url = reverse("register-teacher")
        payload = {
            "email": "prof@example.com",
            "username": "prof1",
            "password": "un-mot-de-passe-solide-42",
            "first_name": "Karim",
            "last_name": "Haddad",
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        profile = TeacherProfile.objects.get(user__email="prof@example.com")
        self.assertFalse(profile.is_active)


class LoginTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="loginuser", email="login@example.com", password="mon-mot-de-passe-123"
        )

    def test_login_with_correct_credentials_returns_tokens(self):
        url = reverse("token_obtain_pair")
        response = self.client.post(
            url, {"username": "loginuser", "password": "mon-mot-de-passe-123"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_login_with_wrong_password_is_rejected(self):
        url = reverse("token_obtain_pair")
        response = self.client.post(
            url, {"username": "loginuser", "password": "mauvais-mot-de-passe"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_login_is_throttled_after_repeated_attempts(self):
        """
        Couvre le throttling ajouté contre le brute-force : au-delà de la
        limite ("login": "10/minute" dans settings.py), l'API doit répondre
        429 plutôt que de continuer à évaluer le mot de passe.
        """
        url = reverse("token_obtain_pair")
        last_response = None
        for _ in range(11):
            last_response = self.client.post(
                url, {"username": "loginuser", "password": "mauvais-mot-de-passe"}, format="json"
            )
        self.assertEqual(last_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class PasswordResetTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="resetuser", email="reset@example.com", password="ancien-mot-de-passe-1"
        )

    def test_request_reset_for_known_email_sends_email(self):
        url = reverse("password-reset-request")
        response = self.client.post(url, {"email": "reset@example.com"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reset@example.com", mail.outbox[0].to)

    def test_request_reset_for_unknown_email_gives_same_generic_response(self):
        """
        Ne doit jamais révéler si un email existe ou non en base (fuite de
        données personnelles) — la réponse et le code HTTP doivent être
        identiques que le compte existe ou pas.
        """
        url = reverse("password-reset-request")
        known = self.client.post(url, {"email": "reset@example.com"}, format="json")
        unknown = self.client.post(url, {"email": "inconnu@example.com"}, format="json")

        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(known.data, unknown.data)
        # Un seul email doit avoir été envoyé (celui du compte existant).
        self.assertEqual(len(mail.outbox), 1)

    def test_confirm_reset_with_valid_token_changes_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        url = reverse("password-reset-confirm")

        response = self.client.post(
            url, {"uid": uid, "token": token, "password": "nouveau-mot-de-passe-2"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("nouveau-mot-de-passe-2"))

    def test_confirm_reset_with_invalid_token_is_rejected(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse("password-reset-confirm")

        response = self.client.post(
            url, {"uid": uid, "token": "token-invalide", "password": "nouveau-mot-de-passe-2"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("nouveau-mot-de-passe-2"))
