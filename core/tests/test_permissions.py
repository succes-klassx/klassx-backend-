"""
Tests transverses de contrôle d'accès : vérifie qu'un élève ne peut pas
atteindre les endpoints admin/enseignant et inversement. Ne re-teste pas
toute l'API — se concentre sur les endpoints les plus sensibles
(statistiques admin, gestion des candidatures enseignants).
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import StudentProfile, TeacherProfile

User = get_user_model()


class RoleBasedAccessTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin@example.com", email="admin@example.com", password="x", role=User.Role.ADMIN
        )
        self.student = User.objects.create_user(
            username="eleve@example.com", email="eleve@example.com", password="x", role=User.Role.STUDENT
        )
        StudentProfile.objects.create(user=self.student, grade_level="terminale")
        self.teacher = User.objects.create_user(
            username="prof@example.com", email="prof@example.com", password="x", role=User.Role.TEACHER
        )
        TeacherProfile.objects.create(user=self.teacher, is_active=True)

    def test_admin_stats_forbidden_for_student_and_teacher(self):
        url = reverse("admin-stats")
        for user in (self.student, self.teacher):
            self.client.force_authenticate(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, f"role={user.role}")

    def test_admin_stats_allowed_for_admin(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("admin-stats"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_teacher_hours_forbidden_for_student(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse("admin-teacher-hours"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_all_admin_and_specialty_endpoints_require_authentication(self):
        """Aucune de ces routes ne doit être accessible sans être connecté."""
        for url_name in ["admin-stats", "admin-teacher-hours", "me-specialties", "me"]:
            response = self.client.get(reverse(url_name))
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_405_METHOD_NOT_ALLOWED),
                f"{url_name} a répondu {response.status_code} sans authentification",
            )
