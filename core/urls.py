from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register("subjects", views.SubjectViewSet, basename="subject")
router.register("teachers", views.TeacherProfileViewSet, basename="teacher")
router.register("selfstudy-plans", views.SelfStudyPlanViewSet, basename="selfstudy-plan")
router.register("selfstudy-content", views.SelfStudyContentViewSet, basename="selfstudy-content")
router.register("class-sessions", views.ClassSessionViewSet, basename="class-session")
router.register("enrollments", views.EnrollmentViewSet, basename="enrollment")
router.register("forum/threads", views.ForumThreadViewSet, basename="forum-thread")
router.register("forum/replies", views.ForumReplyViewSet, basename="forum-reply")
router.register("materials", views.MaterialViewSet, basename="material")
router.register("group-announcements", views.GroupAnnouncementViewSet, basename="group-announcement")
router.register("group-requests", views.GroupRequestViewSet, basename="group-request")
router.register("group-assignments", views.GroupAssignmentViewSet, basename="group-assignment")
router.register("series-memberships", views.SeriesMembershipViewSet, basename="series-membership")
router.register("public/faq", views.FAQViewSet, basename="public-faq")

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/register-teacher/", views.TeacherRegisterView.as_view(), name="register-teacher"),
    path("auth/register-affiliate/", views.AffiliateRegisterView.as_view(), name="register-affiliate"),
    path("auth/password-reset/", views.PasswordResetRequestView.as_view(), name="password-reset-request"),
    path("auth/password-reset/confirm/", views.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("auth/login/", views.LoginView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", views.MeView.as_view(), name="me"),
    path("me/referrals/", views.MyReferralsView.as_view(), name="me-referrals"),
    path("me/teacher-hours/", views.MyTeacherHoursView.as_view(), name="me-teacher-hours"),
    path("me/specialties/", views.MySpecialtiesView.as_view(), name="me-specialties"),
    path("me/payment-method/setup/", views.PaymentMethodSetupView.as_view(), name="payment-method-setup"),

    # Landing page (public, unauthenticated) — see PublicTeacherSerializer /
    # FAQViewSet / StaticPageDetailView.
    path("public/teachers/", views.PublicTeachersView.as_view(), name="public-teachers"),
    path("public/teachers/<int:pk>/", views.PublicTeacherDetailView.as_view(), name="public-teacher-detail"),
    path("public/pricing/", views.PublicPricingView.as_view(), name="public-pricing"),
    path("public/promo-codes/validate/", views.PublicValidatePromoCodeView.as_view(), name="public-promo-code-validate"),
    path("public/pages/<slug:slug>/", views.StaticPageDetailView.as_view(), name="public-page"),
    path("public/newsletter/", views.PublicNewsletterSubscribeView.as_view(), name="public-newsletter"),

    # Teacher self-service settings (autonomous scheduling model) — must
    # come before the router include, so "me" isn't swallowed by the
    # router's "teachers/<pk>/" pattern.
    path("teachers/me/", views.TeacherSettingsView.as_view(), name="teacher-settings"),
    path("teachers/me/google/connect/", views.TeacherGoogleConnectView.as_view(), name="teacher-google-connect"),
    path("teachers/me/google/callback/", views.TeacherGoogleCallbackView.as_view(), name="teacher-google-callback"),
    path("teachers/me/google/disconnect/", views.TeacherGoogleDisconnectView.as_view(), name="teacher-google-disconnect"),

    path("admin/stats/", views.AdminStatsView.as_view(), name="admin-stats"),
    path("admin/teacher-hours/", views.AdminTeacherHoursView.as_view(), name="admin-teacher-hours"),
    path("admin/referrals/", views.AdminReferralsView.as_view(), name="admin-referrals"),
    path("admin/referrals/<int:referrer_id>/mark-paid/", views.AdminMarkReferralPaidView.as_view(), name="admin-referrals-mark-paid"),
    path("admin/assign-group/", views.AdminAssignGroupView.as_view(), name="admin-assign-group"),
    path("admin/schedule-group/", views.AdminScheduleGroupView.as_view(), name="admin-schedule-group"),
    path("individual-bookings/", views.IndividualBookingView.as_view(), name="individual-booking"),
    path("subscriptions/checkout/", views.SubscriptionCheckoutView.as_view(), name="subscription-checkout"),
    path("webhooks/stripe/", views.StripeWebhookView.as_view(), name="stripe-webhook"),
    path("webhooks/konnect/", views.KonnectWebhookView.as_view(), name="konnect-webhook"),
    path("", include(router.urls)),
]
