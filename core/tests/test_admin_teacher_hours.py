"""
Tests de GET /admin/teacher-hours/ — la fonctionnalité de rémunération
demandée pour l'admin (voir AdminTeacherHoursView / AdminDashboard.jsx).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import ClassSession, StudentProfile, Subject, TeacherProfile

User = get_user_model()


def make_admin():
    return User.objects.create_user(
        username="admin@example.com", email="admin@example.com", password="x", role=User.Role.ADMIN
    )


def make_teacher(email="prof@example.com"):
    user = User.objects.create_user(username=email, email=email, password="x", role=User.Role.TEACHER)
    return TeacherProfile.objects.create(user=user, is_active=True)


class AdminTeacherHoursTests(APITestCase):
    def setUp(self):
        self.admin = make_admin()
        self.teacher = make_teacher()
        self.subject = Subject.objects.create(name="Maths", code="maths")
        self.client.force_authenticate(self.admin)
        self.url = reverse("admin-teacher-hours")

    def make_past_session(self, hours_ago_start, duration_hours, status_=ClassSession.Status.SCHEDULED, teacher=None):
        now = timezone.now()
        start = now - timedelta(hours=hours_ago_start)
        return ClassSession.objects.create(
            subject=self.subject,
            level="terminale",
            group_tier=ClassSession.GroupTier.INDIVIDUAL,
            max_capacity=1,
            assigned_teacher=teacher or self.teacher,
            start_time=start,
            end_time=start + timedelta(hours=duration_hours),
            status=status_,
        )

    def test_only_admin_can_access(self):
        teacher_user = self.teacher.user
        self.client.force_authenticate(teacher_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_counts_past_session_hours_for_current_month(self):
        # 2h il y a 3 jours (ce mois) + 1h30 il y a 40 jours (mois dernier, ignoré).
        self.make_past_session(hours_ago_start=72, duration_hours=2)
        self.make_past_session(hours_ago_start=40 * 24, duration_hours=1.5)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        row = next(t for t in response.data["teachers"] if t["teacher_id"] == self.teacher.id)
        self.assertEqual(row["hours_this_month"], 2.0)
        self.assertEqual(row["sessions_this_month"], 1)

    def test_future_session_is_not_counted(self):
        now = timezone.now()
        ClassSession.objects.create(
            subject=self.subject,
            level="terminale",
            group_tier=ClassSession.GroupTier.INDIVIDUAL,
            max_capacity=1,
            assigned_teacher=self.teacher,
            start_time=now + timedelta(days=1),
            end_time=now + timedelta(days=1, hours=1),
        )
        response = self.client.get(self.url)
        row = next(t for t in response.data["teachers"] if t["teacher_id"] == self.teacher.id)
        self.assertEqual(row["hours_this_month"], 0)

    def test_cancelled_session_is_not_counted(self):
        self.make_past_session(hours_ago_start=5, duration_hours=3, status_=ClassSession.Status.CANCELLED)
        response = self.client.get(self.url)
        row = next(t for t in response.data["teachers"] if t["teacher_id"] == self.teacher.id)
        self.assertEqual(row["hours_this_month"], 0)

    def test_session_without_assigned_teacher_is_not_counted(self):
        now = timezone.now()
        ClassSession.objects.create(
            subject=self.subject,
            level="terminale",
            group_tier=ClassSession.GroupTier.INDIVIDUAL,
            max_capacity=1,
            assigned_teacher=None,
            start_time=now - timedelta(hours=5),
            end_time=now - timedelta(hours=3),
        )
        response = self.client.get(self.url)
        # Aucun enseignant n'a de session non assignée à comptabiliser —
        # la liste ne doit pas planter, juste ne rien montrer pour ça.
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_invalid_month_param_returns_400(self):
        response = self.client.get(self.url, {"month": "pas-un-mois"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_explicit_month_param_filters_correctly(self):
        # Session il y a 40 jours : hors mois courant, mais on la retrouve
        # en demandant explicitement le bon mois.
        session = self.make_past_session(hours_ago_start=40 * 24, duration_hours=1.5)
        target_month = session.start_time.strftime("%Y-%m")

        response = self.client.get(self.url, {"month": target_month})
        row = next(t for t in response.data["teachers"] if t["teacher_id"] == self.teacher.id)
        self.assertEqual(row["hours_this_month"], 1.5)
