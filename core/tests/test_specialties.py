"""
Tests de PATCH /me/specialties/ — le champ qui manquait dans le dashboard
élève (voir Dashboard.jsx : MySpecialtiesSection) et son pendant backend.
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


def make_specialty_subjects():
    return [
        Subject.objects.create(name=n, code=n.lower(), subject_type=Subject.SubjectType.SPECIALTY)
        for n in ["Maths", "SES", "HGGSP", "NSI"]
    ]


class MySpecialtiesTests(APITestCase):
    def setUp(self):
        self.user = make_student()
        self.subjects = make_specialty_subjects()
        self.client.force_authenticate(self.user)
        self.url = reverse("me-specialties")

    def test_student_can_set_specialties_within_limits(self):
        maths, ses, hggsp, nsi = self.subjects
        response = self.client.patch(
            self.url,
            {
                "premiere_specialties": [maths.id, ses.id, hggsp.id],
                "terminale_specialties": [maths.id, ses.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.user.student_profile.refresh_from_db()
        self.assertEqual(
            set(self.user.student_profile.premiere_specialties.values_list("id", flat=True)),
            {maths.id, ses.id, hggsp.id},
        )
        self.assertEqual(
            set(self.user.student_profile.terminale_specialties.values_list("id", flat=True)),
            {maths.id, ses.id},
        )

    def test_more_than_3_premiere_specialties_is_rejected(self):
        ids = [s.id for s in self.subjects]  # 4 matières
        response = self.client.patch(self.url, {"premiere_specialties": ids}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_more_than_2_terminale_specialties_is_rejected(self):
        ids = [s.id for s in self.subjects[:3]]  # 3 matières
        response = self.client.patch(self.url, {"terminale_specialties": ids}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anonymous_user_cannot_update_specialties(self):
        self.client.force_authenticate(None)
        response = self.client.patch(self.url, {"premiere_specialties": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_teacher_cannot_update_specialties(self):
        """
        MySpecialtiesView est réservée aux élèves (permission IsStudent) —
        un enseignant n'a pas de student_profile, donc get_object() plante
        si la permission ne le bloque pas avant. Ce test garantit que la
        permission fait bien son travail.
        """
        teacher = User.objects.create_user(
            username="prof@example.com", email="prof@example.com", password="x", role=User.Role.TEACHER
        )
        TeacherProfile.objects.create(user=teacher, is_active=True)
        self.client.force_authenticate(teacher)

        response = self.client.patch(self.url, {"premiere_specialties": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
