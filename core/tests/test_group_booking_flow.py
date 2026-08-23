"""
Tests du flux de réservation de groupe — la partie la plus complexe du
projet (spec 5.5/5.6, voir core/views.py) :

  élève -> GroupRequest (pending)
        -> admin bundle des requêtes assorties -> GroupAssignment (AdminAssignGroupView)
        -> enseignant choisit le(s) créneau(x) -> GroupAssignmentViewSet.schedule
           -> crée ClassSeries + ClassSession + Enrollment + SeriesMembership pour chaque élève

Stripe (charge_saved_payment_method) est mocké — le fallback Jitsi de
core/services/video.py n'appelle rien en réseau, donc il est laissé tel
quel plutôt que mocké.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import (
    ClassSession, Enrollment, GroupAssignment, GroupRequest, SeriesMembership,
    StudentProfile, Subject, TeacherProfile,
)

User = get_user_model()


def make_student(email, adult=True):
    user = User.objects.create_user(username=email, email=email, password="x", role=User.Role.STUDENT)
    dob = timezone.localdate().replace(year=timezone.localdate().year - (20 if adult else 16))
    StudentProfile.objects.create(user=user, bac_type="general", grade_level="terminale", date_of_birth=dob)
    return user


def make_teacher(email="prof@example.com", is_active=True):
    user = User.objects.create_user(username=email, email=email, password="x", role=User.Role.TEACHER)
    return TeacherProfile.objects.create(user=user, is_active=is_active)


def make_subject(**overrides):
    defaults = dict(name="Mathématiques", code="maths", level=Subject.Level.BOTH,
                     bac_type="general", subject_type=Subject.SubjectType.COMMON_CORE)
    defaults.update(overrides)
    return Subject.objects.create(**defaults)


class GroupRequestTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.student = make_student("demandeur@example.com")
        self.client.force_authenticate(user=self.student)

    def test_create_group_request_requires_weekly_hours(self):
        url = reverse("group-request-list")
        response = self.client.post(
            url, {"subject": self.subject.id, "level": "terminale", "group_tier": "GROUP_4"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("weekly_hours", response.data)

    def test_create_group_request_ok(self):
        url = reverse("group-request-list")
        response = self.client.post(
            url,
            {"subject": self.subject.id, "level": "terminale", "group_tier": "GROUP_4", "weekly_hours": 6},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["status"], GroupRequest.Status.PENDING)
        self.assertEqual(GroupRequest.objects.get().student, self.student)

    def test_cancel_pending_request(self):
        gr = GroupRequest.objects.create(
            student=self.student, subject=self.subject, level="terminale",
            group_tier="GROUP_4", weekly_hours=6,
        )
        url = reverse("group-request-cancel", args=[gr.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        gr.refresh_from_db()
        self.assertEqual(gr.status, GroupRequest.Status.CANCELLED)

    def test_cannot_cancel_already_assigned_request(self):
        teacher = make_teacher()
        assignment = GroupAssignment.objects.create(
            subject=self.subject, level="terminale", group_tier="GROUP_4", weekly_hours=6, teacher=teacher,
        )
        gr = GroupRequest.objects.create(
            student=self.student, subject=self.subject, level="terminale", group_tier="GROUP_4",
            weekly_hours=6, status=GroupRequest.Status.TEACHER_ASSIGNED, group_assignment=assignment,
        )
        url = reverse("group-request-cancel", args=[gr.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_only_sees_own_requests(self):
        other_student = make_student("autre@example.com")
        GroupRequest.objects.create(
            student=other_student, subject=self.subject, level="terminale", group_tier="GROUP_4", weekly_hours=6,
        )
        GroupRequest.objects.create(
            student=self.student, subject=self.subject, level="terminale", group_tier="GROUP_4", weekly_hours=6,
        )
        url = reverse("group-request-list")
        response = self.client.get(url)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["student"], self.student.id)

    def test_pending_summary_groups_and_counts(self):
        admin = User.objects.create_user(username="admin2", email="admin2@example.com", password="x", role=User.Role.ADMIN)
        s2 = make_student("s2@example.com")
        s3 = make_student("s3@example.com")
        for s in (self.student, s2):
            GroupRequest.objects.create(
                student=s, subject=self.subject, level="terminale", group_tier="GROUP_4", weekly_hours=6,
            )
        GroupRequest.objects.create(
            student=s3, subject=self.subject, level="terminale", group_tier="GROUP_3", weekly_hours=8,
        )
        self.client.force_authenticate(user=admin)
        url = reverse("group-request-pending-summary")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        counts = {(row["group_tier"], row["weekly_hours"]): row["count"] for row in response.data}
        self.assertEqual(counts[("GROUP_4", 6)], 2)
        self.assertEqual(counts[("GROUP_3", 8)], 1)

    def test_pending_summary_forbidden_for_non_admin(self):
        url = reverse("group-request-pending-summary")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AdminAssignGroupViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.admin = User.objects.create_user(username="admin3", email="admin3@example.com", password="x", role=User.Role.ADMIN)
        self.teacher = make_teacher()
        self.client.force_authenticate(user=self.admin)

    def make_requests(self, n, **overrides):
        defaults = dict(subject=self.subject, level="terminale", group_tier="GROUP_4", weekly_hours=6)
        defaults.update(overrides)
        requests = []
        for i in range(n):
            student = make_student(f"req{i}-{overrides.get('group_tier', 'g4')}@example.com")
            requests.append(GroupRequest.objects.create(student=student, **defaults))
        return requests

    def test_bundles_matching_requests_into_assignment(self):
        requests = self.make_requests(3)
        url = reverse("admin-assign-group")
        response = self.client.post(
            url, {"request_ids": [r.id for r in requests], "teacher_id": self.teacher.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        assignment = GroupAssignment.objects.get()
        self.assertEqual(assignment.teacher, self.teacher)
        for r in requests:
            r.refresh_from_db()
            self.assertEqual(r.status, GroupRequest.Status.TEACHER_ASSIGNED)
            self.assertEqual(r.group_assignment, assignment)

    def test_rejects_mismatched_criteria(self):
        r1 = self.make_requests(1, group_tier="GROUP_4", weekly_hours=6)[0]
        r2 = self.make_requests(1, group_tier="GROUP_3", weekly_hours=8)[0]
        url = reverse("admin-assign-group")
        response = self.client.post(
            url, {"request_ids": [r1.id, r2.id], "teacher_id": self.teacher.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(GroupAssignment.objects.count(), 0)

    def test_rejects_more_students_than_tier_capacity(self):
        # GROUP_3 => capacité 3, on en tente 4.
        requests = self.make_requests(4, group_tier="GROUP_3", weekly_hours=6)
        url = reverse("admin-assign-group")
        response = self.client.post(
            url, {"request_ids": [r.id for r in requests], "teacher_id": self.teacher.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_inactive_teacher(self):
        inactive_teacher = make_teacher("inactif@example.com", is_active=False)
        requests = self.make_requests(2)
        url = reverse("admin-assign-group")
        response = self.client.post(
            url, {"request_ids": [r.id for r in requests], "teacher_id": inactive_teacher.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_forbidden_for_non_admin(self):
        requests = self.make_requests(2)
        self.client.force_authenticate(user=requests[0].student)
        url = reverse("admin-assign-group")
        response = self.client.post(
            url, {"request_ids": [r.id for r in requests], "teacher_id": self.teacher.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class GroupAssignmentScheduleTests(APITestCase):
    """La partie la plus complexe : GroupAssignmentViewSet.schedule."""

    def setUp(self):
        cache.clear()
        self.subject = make_subject()
        self.teacher = make_teacher()
        self.students = [make_student(f"membre{i}@example.com") for i in range(3)]

        self.assignment = GroupAssignment.objects.create(
            subject=self.subject, level="terminale", group_tier="GROUP_4",
            weekly_hours=6, teacher=self.teacher, is_billable=True,
        )
        for s in self.students:
            GroupRequest.objects.create(
                student=s, subject=self.subject, level="terminale", group_tier="GROUP_4",
                weekly_hours=6, status=GroupRequest.Status.TEACHER_ASSIGNED, group_assignment=self.assignment,
            )
        self.client.force_authenticate(user=self.teacher.user)

    def next_monday_slot(self, days_ahead=7, hour=18, duration_hours=2):
        start = (timezone.now() + timedelta(days=days_ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)
        return {
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=duration_hours)).isoformat(),
            "ends_on": (start + timedelta(days=90)).date().isoformat(),
        }

    @patch("core.views.payments.charge_saved_payment_method")
    def test_first_slot_creates_series_sessions_enrollments_and_billable_memberships(self, mock_charge):
        mock_charge.return_value = None  # pas de carte enregistrée -> fallback silencieux, pas d'erreur
        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        response = self.client.post(url, {"slots": [self.next_monday_slot()]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        session = ClassSession.objects.get(group_assignment=self.assignment)
        self.assertEqual(session.assigned_teacher, self.teacher)
        self.assertEqual(Enrollment.objects.filter(class_session=session).count(), 3)

        memberships = SeriesMembership.objects.filter(series=session.series)
        self.assertEqual(memberships.count(), 3)
        self.assertTrue(all(m.is_billable for m in memberships))
        self.assertTrue(all(m.monthly_price_cents > 0 for m in memberships))

        self.assignment.refresh_from_db()
        for gr in GroupRequest.objects.filter(group_assignment=self.assignment):
            self.assertEqual(gr.status, GroupRequest.Status.SCHEDULED)
            self.assertIsNotNone(gr.resulting_enrollment_id)

    @patch("core.views.payments.charge_saved_payment_method")
    def test_second_call_adds_extra_slot_without_rebilling(self, mock_charge):
        mock_charge.return_value = None
        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        self.client.post(url, {"slots": [self.next_monday_slot(days_ahead=7)]}, format="json")

        response = self.client.post(url, {"slots": [self.next_monday_slot(days_ahead=10, hour=16)]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.assertEqual(ClassSession.objects.filter(group_assignment=self.assignment).count(), 2)
        # Le 2e créneau ne doit générer AUCUNE ligne SeriesMembership facturable
        # supplémentaire — une seule série est facturable pour ce package.
        all_memberships = SeriesMembership.objects.filter(series__group_assignment=self.assignment)
        billable = all_memberships.filter(is_billable=True)
        non_billable = all_memberships.filter(is_billable=False)
        self.assertEqual(billable.count(), 3)   # les 3 du 1er créneau
        self.assertEqual(non_billable.count(), 3)  # les 3 du 2e créneau, non facturables
        # charge_saved_payment_method n'est appelé que pour les memberships facturables.
        self.assertEqual(mock_charge.call_count, 3)

    @patch("core.views.payments.charge_saved_payment_method")
    def test_non_billable_assignment_never_charges(self, mock_charge):
        self.assignment.is_billable = False
        self.assignment.save(update_fields=["is_billable"])

        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        response = self.client.post(url, {"slots": [self.next_monday_slot()]}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        mock_charge.assert_not_called()
        memberships = SeriesMembership.objects.filter(series__group_assignment=self.assignment)
        self.assertTrue(all(not m.is_billable for m in memberships))
        self.assertTrue(all(m.monthly_price_cents == 0 for m in memberships))

    def test_missing_slots_is_rejected(self):
        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_ends_on_on_first_slot_is_rejected(self):
        slot = self.next_monday_slot()
        del slot["ends_on"]
        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        response = self.client.post(url, {"slots": [slot]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_teacher_cannot_schedule_this_group(self):
        other_teacher = make_teacher("autreprof@example.com")
        self.client.force_authenticate(user=other_teacher.user)
        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        response = self.client.post(url, {"slots": [self.next_monday_slot()]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)  # get_queryset filtre déjà par enseignant

    @patch("core.views.payments.charge_saved_payment_method")
    def test_manual_meeting_url_is_used_when_provided(self, mock_charge):
        mock_charge.return_value = None
        url = reverse("group-assignment-schedule", args=[self.assignment.id])
        response = self.client.post(
            url,
            {"slots": [self.next_monday_slot()], "meeting_url": "https://meet.google.com/abc-defg-hij"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        session = ClassSession.objects.get(group_assignment=self.assignment)
        self.assertEqual(session.meeting_url, "https://meet.google.com/abc-defg-hij")
