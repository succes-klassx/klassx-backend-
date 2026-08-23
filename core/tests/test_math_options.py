"""
Maths Expertes / Maths Complémentaires — "option mathématiques" de
Terminale, PAS une 3e spécialité (voir backend core/models.py:
Subject.SubjectType.MATH_OPTION / StudentProfile.terminale_math_option).
Expertes suppose d'avoir gardé Mathématiques en spécialité de Terminale ;
Complémentaires, l'inverse — les deux sont mutuellement exclusives.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import BacType, StudentProfile, Subject, TeacherProfile

User = get_user_model()


def make_student(email="eleve@example.com", bac_type=BacType.GENERAL):
    user = User.objects.create_user(username=email, email=email, password="x", role=User.Role.STUDENT)
    StudentProfile.objects.create(user=user, bac_type=bac_type, grade_level="terminale")
    return user


def make_subjects():
    maths = Subject.objects.create(name="Mathématiques", code="gen-maths", subject_type=Subject.SubjectType.SPECIALTY)
    ses = Subject.objects.create(name="SES", code="gen-ses", subject_type=Subject.SubjectType.SPECIALTY)
    expertes = Subject.objects.create(
        name="Mathématiques Expertes", code="gen-maths-expertes",
        subject_type=Subject.SubjectType.MATH_OPTION, level=Subject.Level.TERMINALE,
    )
    complementaires = Subject.objects.create(
        name="Mathématiques Complémentaires", code="gen-maths-complementaires",
        subject_type=Subject.SubjectType.MATH_OPTION, level=Subject.Level.TERMINALE,
    )
    return maths, ses, expertes, complementaires


class MathOptionSpecialtiesUpdateTests(APITestCase):
    def setUp(self):
        self.maths, self.ses, self.expertes, self.complementaires = make_subjects()
        self.user = make_student()
        self.client.force_authenticate(self.user)
        self.url = reverse("me-specialties")

    def test_maths_expertes_requires_maths_kept_as_specialty(self):
        response = self.client.patch(
            self.url,
            {"terminale_specialties": [self.ses.id], "terminale_math_option": self.expertes.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_maths_expertes_accepted_when_maths_is_kept(self):
        response = self.client.patch(
            self.url,
            {"terminale_specialties": [self.maths.id, self.ses.id], "terminale_math_option": self.expertes.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.student_profile.refresh_from_db()
        self.assertEqual(self.user.student_profile.terminale_math_option_id, self.expertes.id)

    def test_maths_complementaires_requires_maths_dropped(self):
        response = self.client.patch(
            self.url,
            {"terminale_specialties": [self.maths.id, self.ses.id], "terminale_math_option": self.complementaires.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_maths_complementaires_accepted_when_maths_dropped(self):
        response = self.client.patch(
            self.url,
            {"terminale_specialties": [self.ses.id], "terminale_math_option": self.complementaires.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.student_profile.refresh_from_db()
        self.assertEqual(self.user.student_profile.terminale_math_option_id, self.complementaires.id)

    def test_math_option_does_not_count_toward_the_2_specialty_cap(self):
        """Expertes + 2 spécialités (dont Maths) doit passer — ce n'est pas une 3e spécialité."""
        autre = Subject.objects.create(name="NSI", code="gen-nsi", subject_type=Subject.SubjectType.SPECIALTY)
        response = self.client.patch(
            self.url,
            {"terminale_specialties": [self.maths.id, autre.id], "terminale_math_option": self.expertes.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_dropping_maths_after_choosing_expertes_is_rejected(self):
        """Si Expertes est déjà choisie et qu'on retire Mathématiques des spécialités dans la même requête, ça doit échouer."""
        self.user.student_profile.terminale_specialties.set([self.maths.id])
        self.user.student_profile.terminale_math_option = self.expertes
        self.user.student_profile.save()

        response = self.client.patch(self.url, {"terminale_specialties": [self.ses.id]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_clearing_math_option_is_allowed(self):
        self.user.student_profile.terminale_specialties.set([self.maths.id])
        self.user.student_profile.terminale_math_option = self.expertes
        self.user.student_profile.save()

        response = self.client.patch(self.url, {"terminale_math_option": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.user.student_profile.refresh_from_db()
        self.assertIsNone(self.user.student_profile.terminale_math_option)


class MathOptionRegistrationTests(APITestCase):
    def setUp(self):
        self.maths, self.ses, self.expertes, self.complementaires = make_subjects()
        self.url = reverse("register")

    def _base_payload(self, **overrides):
        payload = {
            "email": "nouveau@example.com", "username": "nouveau@example.com", "password": "Sup3rSecret!",
            "first_name": "Jean", "last_name": "Dupont", "country": "France",
            "bac_type": "general", "grade_level": "terminale", "date_of_birth": "2007-01-01",
        }
        payload.update(overrides)
        return payload

    def test_registration_rejects_inconsistent_math_option(self):
        payload = self._base_payload(
            terminale_specialties=[self.ses.id], terminale_math_option=self.expertes.id,
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_registration_accepts_consistent_math_option(self):
        payload = self._base_payload(
            terminale_specialties=[self.maths.id, self.ses.id], terminale_math_option=self.expertes.id,
        )
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        user = User.objects.get(email="nouveau@example.com")
        self.assertEqual(user.student_profile.terminale_math_option_id, self.expertes.id)


class MathOptionBookingAccessTests(APITestCase):
    """validate_specialty_access ne doit accepter Maths Expertes/Complémentaires que si c'est le choix du profil — jamais via terminale_specialties."""

    def setUp(self):
        self.maths, self.ses, self.expertes, self.complementaires = make_subjects()
        self.user = make_student()
        self.user.student_profile.terminale_specialties.set([self.maths.id, self.ses.id])
        self.user.student_profile.terminale_math_option = self.expertes
        self.user.student_profile.save()
        self.client.force_authenticate(self.user)

    def test_group_request_for_chosen_math_option_is_allowed(self):
        response = self.client.post(
            reverse("group-request-list"),
            {"subject": self.expertes.id, "level": "terminale", "group_tier": "GROUP_5", "weekly_hours": 6},
            format="json",
        )
        self.assertNotEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_group_request_for_other_math_option_is_rejected(self):
        response = self.client.post(
            reverse("group-request-list"),
            {"subject": self.complementaires.id, "level": "terminale", "group_tier": "GROUP_5", "weekly_hours": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
