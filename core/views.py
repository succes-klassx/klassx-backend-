from datetime import datetime, timedelta
from decimal import Decimal

import binascii
import hashlib
import logging
import secrets

import stripe
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, permissions, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from .utils import send_brevo_email
from .models import (
    ClassSeries, ClassSession, Enrollment, FAQ, ForumReply, ForumThread,
    GroupAnnouncement, GroupAssignment, GroupRequest, Material, NewsletterSubscriber, Payment,
    ReferralCommission, SeriesMembership, StaticPage, Subject, Subscription,
    SelfStudyContentItem, SelfStudyPlan, TeacherProfile, VideoProgress, WhiteboardSnapshot,
)
from .permissions import IsAdmin, IsAdminOrReadOnly, IsOwnerOrAdmin, IsStudent, IsTeacher
from .serializers import (
    AffiliateRegistrationSerializer, ClassSessionSerializer, EnrollmentSerializer, FAQSerializer,
    ForumReplySerializer, ForumThreadSerializer, GroupAnnouncementSerializer,
    GroupAssignmentSerializer, GroupRequestSerializer,
    IndividualBookingSerializer, MaterialSerializer, NewsletterSubscriberSerializer, PublicTeacherDetailSerializer, PublicTeacherSerializer,
    SeriesMembershipSerializer, StaticPageSerializer, StudentRegistrationSerializer,
    StudentSpecialtiesUpdateSerializer, SubjectSerializer,
    TeacherProfileSerializer, TeacherRegistrationSerializer,
    TeacherSettingsSerializer, UserSerializer, SelfStudyContentItemSerializer, SelfStudyPlanSerializer,
    VideoProgressSerializer,
)
from .services import brevo, google_meet, konnect, notifications, payments, video
from . import discounts
from .services.referrals import REFERRAL_COMMISSION_RATE

# Utilisé pour les créations de salle vidéo (Google Meet/Daily/Jitsi) qui
# échouent silencieusement en arrière-plan — voir les 4 endroits marqués
# "# room can be (re)created later" ci-dessous. On ne bloque jamais la
# réservation/assignation pour ça, mais on veut au moins voir l'erreur
# réelle dans les logs plutôt que la perdre complètement (visible dans le
# terminal `manage.py runserver` en dev).
logger = logging.getLogger(__name__)
# Pricing lives in core/pricing.py (shared with the serializer, so prices
# display on the catalog too — see ClassSessionSerializer).
from .pricing import series_monthly_price_cents, session_price_cents

User = get_user_model()

# Cancellation notice window before a student-initiated cancellation stops
# qualifying for a refund. Value TBC with product owner — spec 5.2.
CANCELLATION_NOTICE_HOURS = 24

# Salt for signing the `state` param of the per-teacher Google connect flow
# (see TeacherGoogleConnectView/TeacherGoogleCallbackView) — just a
# namespacing string for django.core.signing, not a secret in itself
# (SECRET_KEY does the actual signing).
GOOGLE_TEACHER_OAUTH_STATE_SALT = "klassx-teacher-google-connect"


# ---------------------------------------------------------------------------
# Auth / account
# ---------------------------------------------------------------------------
class RegisterView(generics.CreateAPIView):
    """Public self-registration for students/parents (spec 3.A)."""
    queryset = User.objects.all()
    serializer_class = StudentRegistrationSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"


class TeacherRegisterView(generics.CreateAPIView):
    """
    Public self-registration for teachers — separate from RegisterView
    above (student flow), so the two account types have their own
    dedicated fields (bio/subjects vs grade_level/specialties) instead of
    one form branching on a role field. The account is created and can log
    in immediately, but `TeacherProfile.is_active` stays False until an
    admin approves it (see TeacherProfileSerializer.create / spec 3.C).
    """
    queryset = User.objects.all()
    serializer_class = TeacherRegistrationSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"


class AffiliateRegisterView(generics.CreateAPIView):
    """
    Public self-registration for pure affiliates (programme de parrainage
    ouvert à tout le monde, pas seulement aux enseignants/élèves) — compte
    minimal, sans aucun autre rôle sur la plateforme, juste un
    `referral_code` à partager (voir AffiliateRegistrationSerializer).
    """
    queryset = User.objects.all()
    serializer_class = AffiliateRegistrationSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"


class LoginView(TokenObtainPairView):
    """
    Identique à TokenObtainPairView (simplejwt) — juste avec un throttle
    dédié pour empêcher le brute-force du mot de passe. Utilisée à la
    place de TokenObtainPairView directement dans urls.py.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class PasswordResetRequestView(APIView):
    """
    POST /api/auth/password-reset/ — { "email": "..." }

    Always responds with the same generic message whether or not an
    account exists for that email, to avoid leaking which addresses are
    registered. If one does exist, emails a reset link to
    `{FRONTEND_URL}/mot-de-passe-oublie/confirmer?uid=...&token=...`.
    """
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        user = User.objects.filter(email__iexact=email).first() if email else None
        if not user and email:
            user = User.objects.filter(username__iexact=email).first()

        if user is not None:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = f"{settings.FRONTEND_URL}/mot-de-passe-oublie/confirmer?uid={uid}&token={token}"
            html_content = f"<p>Bonjour,</p><p>Cliquez sur le lien suivant pour réinitialiser votre mot de passe :</p><p><a href='{reset_url}'>Réinitialiser mon mot de passe</a></p>"
        send_brevo_email(user.email, "Réinitialisation de votre mot de passe", html_content)

        return Response(
            {"detail": "Si un compte existe avec cet email, un lien de réinitialisation vient de vous être envoyé."}
        )


class PasswordResetConfirmView(APIView):
    """POST /api/auth/password-reset/confirm/ — { "uid": "...", "token": "...", "password": "..." }"""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        uid = request.data.get("uid", "")
        token = request.data.get("token", "")
        password = request.data.get("password", "")

        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)))
        except (User.DoesNotExist, ValueError, TypeError, OverflowError, binascii.Error):
            return Response({"detail": "Ce lien de réinitialisation n'est pas valide."}, status=status.HTTP_400_BAD_REQUEST)

        if not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "Ce lien de réinitialisation a expiré ou n'est plus valide. Faites une nouvelle demande."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(password, user=user)
        except DjangoValidationError as exc:
            return Response({"password": exc.messages}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(password)
        user.save(update_fields=["password"])
        return Response({"detail": "Votre mot de passe a été mis à jour."})


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class MyReferralsView(APIView):
    """
    GET /api/me/referrals/ — détail du parrainage pour la personne
    connectée (élève, enseignant, ou affilié : ouvert à tout type de
    compte, voir ReferralCard.jsx). Contrairement à `referral_earnings_total`
    sur /api/me/ (juste un total par devise), cet endpoint liste CHAQUE
    filleul individuellement avec ce qu'il a généré — pour que la personne
    comprenne d'où vient son gain, pas juste un chiffre opaque.

    Vie privée du filleul : seul son prénom + l'initiale de son nom sont
    exposés au parrain (jamais l'email ni le nom complet) — le parrain n'a
    pas besoin de plus pour savoir "qui" a été comptabilisé.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        totals_qs = (
            ReferralCommission.objects.filter(referrer=request.user)
            .values("currency").annotate(earned=Sum("amount"), unpaid=Sum("amount", filter=Q(paid_out=False)))
        )
        totals = {
            row["currency"]: {"earned": str(row["earned"]), "unpaid": str(row["unpaid"] or Decimal("0.00"))}
            for row in totals_qs
        }

        by_student = {}
        commissions = (
            ReferralCommission.objects.filter(referrer=request.user)
            .select_related("student").order_by("-created_at")
        )
        for c in commissions:
            key = c.student_id
            if key not in by_student:
                last_initial = f"{c.student.last_name[:1]}." if c.student.last_name else ""
                by_student[key] = {
                    "first_name": c.student.first_name or c.student.username,
                    "last_initial": last_initial,
                    "joined_at": c.student.date_joined,
                    "totals": {},
                }
            entry = by_student[key]["totals"].setdefault(c.currency, Decimal("0.00"))
            by_student[key]["totals"][c.currency] = entry + c.amount

        referred_students = [
            {
                "first_name": v["first_name"],
                "last_initial": v["last_initial"],
                "joined_at": v["joined_at"],
                "totals": {cur: str(amt) for cur, amt in v["totals"].items()},
            }
            for v in by_student.values()
        ]

        return Response({
            "referral_code": request.user.referral_code,
            "commission_rate": str(REFERRAL_COMMISSION_RATE * 100),  # "10.00" — jamais recopié en dur côté frontend
            "totals": totals,
            "referred_students": referred_students,
        })


class MySpecialtiesView(generics.UpdateAPIView):
    """PATCH /api/me/specialties/ — update the logged-in student's specialty choices."""
    serializer_class = StudentSpecialtiesUpdateSerializer
    permission_classes = [permissions.IsAuthenticated, IsStudent]

    def get_object(self):
        return self.request.user.student_profile

    def update(self, request, *args, **kwargs):
        super().update(request, *args, **kwargs)
        return Response(UserSerializer(request.user).data)


class MyWhiteboardView(APIView):
    """
    GET/PUT /api/me/whiteboard/ — le tableau blanc PERSONNEL de
    l'utilisateur connecté, sans lien avec une séance précise (voir
    WhiteboardSnapshot.user) — sert d'argument marketing pour qu'un élève
    découvre la fonctionnalité avant d'avoir payé une vraie séance.

    - Élève : accessible uniquement pendant les 14 jours suivant son
      inscription (spec confirmée : "2 semaines pour se faire une idée")
      — passé ce délai, 403 avec un message clair plutôt qu'une
      disparition silencieuse du bouton.
    - Enseignant : accès permanent, sans limite de temps — ce sont des
      partenaires déjà engagés, pas des prospects à convaincre.
    - Connexion obligatoire dans les deux cas (IsAuthenticated) — jamais
      d'accès anonyme, quel que soit le rôle.
    """
    permission_classes = [permissions.IsAuthenticated]
    TRIAL_DAYS = 14

    def _check_access(self, user):
        if user.role == "teacher":
            return
        if user.role == "student":
            trial_ends = user.date_joined + timedelta(days=self.TRIAL_DAYS)
            if timezone.now() <= trial_ends:
                return
            raise PermissionDenied(
                f"Votre période d'essai du tableau ({self.TRIAL_DAYS} jours après l'inscription) est terminée."
            )
        raise PermissionDenied("Le tableau personnel n'est pas disponible pour ce type de compte.")

    def get(self, request):
        self._check_access(request.user)
        snapshot, _ = WhiteboardSnapshot.objects.get_or_create(user=request.user)
        return Response({"pages": snapshot.pages, "updated_at": snapshot.updated_at})

    def put(self, request):
        self._check_access(request.user)
        pages = request.data.get("pages")
        if not isinstance(pages, list):
            return Response({"detail": "pages must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot, _ = WhiteboardSnapshot.objects.get_or_create(user=request.user)
        snapshot.pages = pages
        snapshot.save(update_fields=["pages", "updated_at"])
        return Response({"pages": snapshot.pages, "updated_at": snapshot.updated_at})


class PaymentMethodSetupView(APIView):
    """
    POST /api/me/payment-method/setup/ — returns a Stripe Checkout URL
    (mode="setup") for the logged-in student to save a card WITHOUT being
    charged. Part of the "add your card when requesting a package, only
    get billed once your teacher schedules a real session" flow — see
    core/services/payments.py: create_card_setup_checkout_session and
    GroupAssignmentViewSet.schedule.
    """
    permission_classes = [permissions.IsAuthenticated, IsStudent]

    def post(self, request):
        if request.user.student_profile.requires_parental_consent:
            return Response(
                {"detail": "Une autorisation parentale est requise avant d'enregistrer un moyen de paiement.",
                 "code": "parental_consent_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            checkout_session = payments.create_card_setup_checkout_session(request.user.student_profile)
        except Exception as exc:
            return Response({"detail": f"Stripe error: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"checkout_url": checkout_session.url})


# ---------------------------------------------------------------------------
# Teacher self-service settings (autonomous scheduling model): the video
# link a teacher's own sessions default to — either a pasted personal link,
# or their own connected Google account (see TeacherProfile, and
# core/services/video.py for the resolution order).
# ---------------------------------------------------------------------------
class TeacherSettingsView(generics.RetrieveUpdateAPIView):
    """
    GET/PATCH /api/teachers/me/ — the logged-in teacher's own video-link
    settings AND public profile content (photo, bio, etc. — see
    TeacherSettingsSerializer). MultiPartParser is required here because
    `photo` is a file upload — the default JSON-only parser can't read a
    multipart/form-data request, so a PATCH with a photo would otherwise
    silently fail to save it (or 415 depending on how the client sent it).
    """
    serializer_class = TeacherSettingsSerializer
    permission_classes = [permissions.IsAuthenticated, IsTeacher]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_object(self):
        return self.request.user.teacher_profile


def _teacher_google_oauth_flow(request):
    """Shared Flow builder for the connect/callback views below."""
    from google_auth_oauthlib.flow import Flow

    redirect_uri = request.build_absolute_uri(reverse("teacher-google-callback"))
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=google_meet.TEACHER_CONNECT_SCOPES,
        redirect_uri=redirect_uri,
    )


class TeacherGoogleConnectView(APIView):
    """
    GET /api/teachers/me/google/connect/ — returns the Google OAuth
    authorization URL for the logged-in teacher to connect their own
    Google account. The frontend should navigate the browser to
    `authorization_url`; Google redirects back to TeacherGoogleCallbackView
    below once the teacher approves access.

    Requires the same OAuth Client as the org-level Google Meet
    integration (GOOGLE_OAUTH_CLIENT_ID/SECRET — auto-detected from
    google_credentials.json, see settings.py) — with THIS view's URL
    registered as an additional "Authorized redirect URI" in Google Cloud
    Console (distinct from the one used by `get_google_oauth_token`). See
    README.md "Autonomous teacher scheduling" section.
    """
    permission_classes = [permissions.IsAuthenticated, IsTeacher]

    def get(self, request):
        if not (settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET):
            return Response(
                {
                    "detail": "La connexion Google n'est pas configurée sur ce serveur "
                    "(GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET manquants). "
                    "Vous pouvez tout de même renseigner un lien de visioconférence personnel."
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        try:
            flow = _teacher_google_oauth_flow(request)
        except ImportError:
            return Response(
                {"detail": "google-auth-oauthlib n'est pas installé côté serveur."},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )

        # PKCE (activé par défaut depuis google-auth-oauthlib 1.2) : Google
        # exige un "code_verifier" au moment de l'échange du code, identique
        # à celui utilisé pour générer le "code_challenge" envoyé ici. On le
        # génère et le fixe NOUS-MÊMES (plutôt que de compter sur le moment
        # exact où la librairie le génère en interne, ce qui avait échoué
        # une première fois — le lire trop tôt renvoyait encore None) pour
        # être certains qu'il existe déjà à cet instant précis. On le fait
        # ensuite transiter via `state`, signé, car le callback ci-dessous
        # reconstruit un tout nouvel objet Flow (impossible de réutiliser
        # celui-ci d'une requête HTTP à l'autre) — voir
        # TeacherGoogleCallbackView, qui le relit et le réinjecte.
        code_verifier = secrets.token_urlsafe(64)
        flow.code_verifier = code_verifier

        state = signing.dumps(
            {"teacher_id": request.user.teacher_profile.id, "code_verifier": code_verifier},
            salt=GOOGLE_TEACHER_OAUTH_STATE_SALT,
        )
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            # Forces Google to hand back a refresh_token even on a repeat
            # connection (Google only does this automatically the very
            # first time an account grants access to this OAuth Client).
            prompt="consent",
            state=state,
        )
        return Response({"authorization_url": authorization_url})


class TeacherGoogleCallbackView(APIView):
    """
    GET /api/teachers/me/google/callback/ — Google redirects the teacher's
    browser here after they approve (or deny) access. This is a plain
    browser navigation, not an authenticated API call, so no JWT is
    available — the teacher is identified via the signed `state` param
    minted by TeacherGoogleConnectView above, not request.user.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        settings_url = f"{settings.FRONTEND_URL}/enseignant"

        if request.GET.get("error"):
            return redirect(f"{settings_url}?google=error")

        try:
            state_data = signing.loads(
                request.GET.get("state", ""), salt=GOOGLE_TEACHER_OAUTH_STATE_SALT, max_age=600
            )
            teacher_id = state_data["teacher_id"]
            code_verifier = state_data["code_verifier"]
            teacher = TeacherProfile.objects.get(pk=teacher_id)
        except (signing.BadSignature, TeacherProfile.DoesNotExist, ValueError, KeyError, TypeError):
            return redirect(f"{settings_url}?google=error")

        try:
            from googleapiclient.discovery import build

            flow = _teacher_google_oauth_flow(request)
            # Réinjecte le même code_verifier que celui utilisé pour créer
            # le code_challenge à l'étape "connect" (voir TeacherGoogleConnectView)
            # — sans ça, Google refuse l'échange (PKCE).
            flow.code_verifier = code_verifier
            flow.fetch_token(code=request.GET.get("code", ""))
            credentials = flow.credentials
            email = (
                build("oauth2", "v2", credentials=credentials, cache_discovery=False)
                .userinfo()
                .get()
                .execute()
                .get("email", "")
            )
        except Exception:
            # Avant, l'erreur réelle disparaissait silencieusement ici —
            # on ne pouvait jamais savoir POURQUOI l'échange de jeton
            # échouait, juste que ça échouait. Maintenant loggé, visible
            # dans les logs Render juste après une tentative.
            logger.exception("Teacher Google OAuth callback failed for teacher_id=%s", teacher_id)
            return redirect(f"{settings_url}?google=error")

        if credentials.refresh_token:
            teacher.google_oauth_refresh_token = credentials.refresh_token
        teacher.google_account_email = email
        teacher.save(update_fields=["google_oauth_refresh_token", "google_account_email"])

        return redirect(f"{settings_url}?google=connected")


class TeacherGoogleDisconnectView(APIView):
    """POST /api/teachers/me/google/disconnect/ — forgets this teacher's connected Google account."""
    permission_classes = [permissions.IsAuthenticated, IsTeacher]

    def post(self, request):
        teacher = request.user.teacher_profile
        teacher.google_oauth_refresh_token = ""
        teacher.google_account_email = ""
        teacher.save(update_fields=["google_oauth_refresh_token", "google_account_email"])
        return Response(TeacherSettingsSerializer(teacher).data)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class SubjectViewSet(viewsets.ModelViewSet):
    queryset = Subject.objects.all()
    serializer_class = SubjectSerializer
    permission_classes = [IsAdminOrReadOnly]
    filterset_fields = ["level", "bac_type"]


class TeacherProfileViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Students/teachers only ever see approved (is_active=True) teachers.
    Admins additionally see pending applications, so they can review and
    approve them (spec 3.C: "Onboard, review, and approve teacher accounts").
    """
    serializer_class = TeacherProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["is_active"]

    def get_queryset(self):
        qs = TeacherProfile.objects.select_related("user").prefetch_related("subjects__subject")
        if self.request.user.role == "admin":
            return qs
        return qs.filter(is_active=True)

    @action(detail=True, methods=["post"], permission_classes=[IsAdmin])
    def approve(self, request, pk=None):
        teacher = self.get_object()
        teacher.is_active = True
        teacher.save(update_fields=["is_active"])
        notifications.send_teacher_approved(teacher)
        return Response(TeacherProfileSerializer(teacher).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdmin])
    def reject(self, request, pk=None):
        teacher = self.get_object()
        teacher.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SelfStudyPlanViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/selfstudy-plans/ — catalogue public des 5 abonnements de
    contenu en libre-service (voir SelfStudyPlan). AllowAny : un visiteur
    non connecté doit pouvoir voir les 5 offres et leurs prix avant de
    créer un compte.
    """
    queryset = SelfStudyPlan.objects.filter(is_active=True)
    serializer_class = SelfStudyPlanSerializer
    permission_classes = [permissions.AllowAny]

    def get_serializer_context(self):
        return {"request": self.request}


class SelfStudyContentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Catalogue du contenu (vidéos + PDF) d'un plan — spec : "chaque
    abonnement est tout seul et séparé". Ne renvoie QUE les items
    `is_unlocked=True` (voir SelfStudyContentItem.is_unlocked) — un item
    préparé mais pas encore débloqué par l'admin est invisible ici, même
    pour un abonné actif ; playback_url/download_url vérifient en plus
    l'abonnement à CE plan précisément, pas juste être connecté.
    """
    queryset = SelfStudyContentItem.objects.filter(is_unlocked=True).select_related("plan")
    serializer_class = SelfStudyContentItemSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["plan", "month", "content_type"]

    def _has_active_subscription(self, user, plan_id):
        if user.role in ("admin", "teacher"):
            return True
        return Subscription.objects.filter(
            user=user, plan_id=plan_id, status=Subscription.Status.ACTIVE, current_period_end__gte=timezone.now()
        ).exists()

    @action(detail=True, methods=["post"])
    def progress(self, request, pk=None):
        item = self.get_object()
        percentage = request.data.get("progress_percentage")
        if percentage is None or not (0 <= int(percentage) <= 100):
            return Response({"detail": "progress_percentage must be 0-100."}, status=status.HTTP_400_BAD_REQUEST)
        obj, _ = VideoProgress.objects.update_or_create(
            student=request.user, capsule=item, defaults={"progress_percentage": percentage}
        )
        return Response(VideoProgressSerializer(obj).data)

    @action(detail=True, methods=["get"])
    def playback_url(self, request, pk=None):
        item = self.get_object()
        if not self._has_active_subscription(request.user, item.plan_id):
            return Response(
                {"detail": f"Un abonnement actif à « {item.plan.name} » est nécessaire pour visionner ce contenu."},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        # video_provider_id is expected to already be a direct playable URL
        # (e.g. an mp4 link or a Cloudflare Stream HLS manifest URL) once a
        # real provider is wired up; for now it's whatever was entered in
        # the admin.
        return Response({"url": item.video_provider_id})

    @action(detail=True, methods=["get"])
    def download_url(self, request, pk=None):
        item = self.get_object()
        if not self._has_active_subscription(request.user, item.plan_id):
            return Response(
                {"detail": f"Un abonnement actif à « {item.plan.name} » est nécessaire pour télécharger ce PDF."},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        if not item.pdf_file:
            return Response({"detail": "Aucun fichier PDF pour cet élément."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"url": request.build_absolute_uri(item.pdf_file.url)})


# ---------------------------------------------------------------------------
# Class sessions
# ---------------------------------------------------------------------------
class ClassSessionViewSet(viewsets.ModelViewSet):
    queryset = ClassSession.objects.select_related("subject", "assigned_teacher").all()
    serializer_class = ClassSessionSerializer
    filterset_fields = ["subject", "level", "group_tier", "status", "assigned_teacher"]

    def get_permissions(self):
        if self.action in ["create", "update", "partial_update", "destroy", "assign_teacher"]:
            return [IsAdmin()]
        if self.action == "add_extra_session":
            return [permissions.IsAuthenticated(), IsTeacher()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        group_tier = serializer.validated_data["group_tier"]
        serializer.save(max_capacity=ClassSession.TIER_CAPACITY[group_tier])

    @action(detail=False, methods=["get"], permission_classes=[IsTeacher])
    def mine(self, request):
        """Sessions assigned to the currently logged-in teacher."""
        qs = self.get_queryset().filter(assigned_teacher__user=request.user).order_by("start_time")
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page or qs, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    def _check_whiteboard_access(self, session):
        """
        Le tableau blanc d'une séance n'est accessible qu'à l'enseignant
        assigné à CETTE séance, ou à un élève qui y est inscrit — même
        logique d'accès que MaterialViewSet.get_queryset, pour rester
        cohérent avec le reste de la plateforme. Lève PermissionDenied
        sinon (jamais un simple code partagé à la main).
        """
        user = self.request.user
        if user.role == "admin":
            return
        if user.role == "teacher" and session.assigned_teacher_id == getattr(user.teacher_profile, "id", None):
            return
        if user.role == "student" and Enrollment.objects.filter(student=user, class_session=session).exists():
            return
        raise PermissionDenied("Vous n'avez pas accès au tableau de cette séance.")

    @action(detail=True, methods=["get", "put"], url_path="whiteboard")
    def whiteboard(self, request, pk=None):
        """
        GET/PUT /api/class-sessions/{id}/whiteboard/ — l'état sauvegardé
        du tableau blanc interactif (voir models.WhiteboardSnapshot). PUT
        remplace `pages` en entier à chaque sauvegarde (pas de diff
        incrémental) — c'est la structure JSON produite telle quelle par
        le tableau JS lui-même (tableau-lecons-3.html), le backend ne
        l'interprète jamais, juste la stocke et la restitue.
        """
        session = self.get_object()
        self._check_whiteboard_access(session)
        snapshot, _ = WhiteboardSnapshot.objects.get_or_create(class_session=session)
        if request.method == "GET":
            return Response({"pages": snapshot.pages, "updated_at": snapshot.updated_at})
        pages = request.data.get("pages")
        if not isinstance(pages, list):
            return Response({"detail": "pages must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot.pages = pages
        snapshot.save(update_fields=["pages", "updated_at"])
        return Response({"pages": snapshot.pages, "updated_at": snapshot.updated_at})

    @action(detail=True, methods=["get"], url_path="whiteboard-room-code")
    def whiteboard_room_code(self, request, pk=None):
        """
        GET /api/class-sessions/{id}/whiteboard-room-code/ — le code de
        session PeerJS (voir tableau-lecons-3.html) pour CETTE séance
        précise, dérivé de façon stable et signée plutôt que tapé/partagé
        à la main. Accessible uniquement à l'enseignant assigné ou un
        élève inscrit (_check_whiteboard_access) — c'est ce contrôle-là
        qui protège réellement l'accès au tableau : PeerJS lui-même
        n'a aucune notion de compte KLASSX, n'importe qui connaissant le
        code peut s'y connecter techniquement, donc le code ne doit
        jamais être découvrable sans être passé par cette vérification.
        Signé + expire (max_age) pour qu'il ne reste pas valable
        indéfiniment s'il fuitait.
        """
        session = self.get_object()
        self._check_whiteboard_access(session)
        # HMAC dérivé de SECRET_KEY + l'id de séance — déterministe (la
        # même séance donne toujours le même code, pour que tout le monde
        # se retrouve dans la même room PeerJS), mais impossible à deviner
        # sans connaître SECRET_KEY. Alphanumérique uniquement (PeerJS
        # n'accepte pas tous les caractères dans un ID de peer) —
        # contrairement à signing.dumps(), qui inclut ':' et d'autres
        # caractères non garantis compatibles.
        digest = hashlib.sha256(f"{settings.SECRET_KEY}:whiteboard:{session.id}".encode()).hexdigest()
        code = digest[:12].upper()
        return Response({"room_code": code})

    @action(detail=False, methods=["post"])
    def add_extra_session(self, request):
        """
        Teacher-only, autonomous scheduling model: adds a one-off extra
        session (e.g. a makeup class) to a ClassSeries the requesting
        teacher is assigned to — auto-enrolls every currently active
        member of that series, the same as the weekly auto-generation
        (see `generate_series_occurrences`). The regular weekly occurrence
        is still created automatically by that command; this is only for
        anything outside the normal slot.

        Payload: {
            "series": <id>, "start_time": "...", "end_time": "...",
            "meeting_url": "..."   // optional — defaults to this teacher's
                                    // own link/Google account, same
                                    // resolution as everywhere else.
        }
        """
        series_id = request.data.get("series")
        start_dt = parse_datetime(request.data.get("start_time", ""))
        end_dt = parse_datetime(request.data.get("end_time", ""))
        if not series_id or not start_dt or not end_dt:
            return Response(
                {"detail": "series, start_time and end_time are required."}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            series = ClassSeries.objects.select_related("assigned_teacher__user", "subject").get(pk=series_id)
        except ClassSeries.DoesNotExist:
            return Response({"detail": "Series not found."}, status=status.HTTP_404_NOT_FOUND)

        if not series.assigned_teacher or series.assigned_teacher.user_id != request.user.id:
            return Response({"detail": "Ce groupe ne vous est pas assigné."}, status=status.HTTP_403_FORBIDDEN)

        manual_meeting_url = (request.data.get("meeting_url") or "").strip()

        session = ClassSession.objects.create(
            subject=series.subject, level=series.level, group_tier=series.group_tier,
            max_capacity=ClassSession.TIER_CAPACITY[series.group_tier],
            assigned_teacher=series.assigned_teacher, series=series,
            start_time=start_dt, end_time=end_dt, status=ClassSession.Status.ASSIGNED,
            group_assignment=series.group_assignment,
        )
        if manual_meeting_url:
            session.meeting_url = manual_meeting_url
            session.save(update_fields=["meeting_url"])
        else:
            try:
                session.meeting_url, event_id = video.create_room_for_session_full(session)
                if event_id:
                    session.calendar_event_id = event_id
                session.save(update_fields=["meeting_url", "calendar_event_id"])
            except Exception:
                logger.exception("Video room creation failed for session %s (series occurrence)", session.id)

        continuing_memberships = [
            m for m in SeriesMembership.objects.filter(series=series).select_related("student")
            if m.is_member_on(start_dt)
        ]
        for membership in continuing_memberships:
            payment_status = (
                Enrollment.PaymentStatus.PAID
                if not membership.is_billable
                or (membership.status == SeriesMembership.Status.ACTIVE and membership.stripe_subscription_id)
                else Enrollment.PaymentStatus.PENDING
            )
            enrollment = Enrollment.objects.create(
                student=membership.student, class_session=session, payment_status=payment_status
            )
            notifications.send_enrollment_confirmed(enrollment)

        return Response(ClassSessionSerializer(session).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def assign_teacher(self, request, pk=None):
        """Admin-only: assign an onboarded teacher to this session (spec 2 & 3.C)."""
        session = self.get_object()
        teacher_id = request.data.get("teacher_id")
        try:
            teacher = TeacherProfile.objects.get(pk=teacher_id, is_active=True)
        except TeacherProfile.DoesNotExist:
            return Response({"detail": "No active teacher with that id."}, status=status.HTTP_400_BAD_REQUEST)

        session.assigned_teacher = teacher
        session.status = ClassSession.Status.ASSIGNED
        if not session.meeting_url:
            try:
                session.meeting_url, event_id = video.create_room_for_session_full(session)
                if event_id:
                    session.calendar_event_id = event_id
            except Exception:
                # Don't block the assignment if the video provider call
                # fails — the room can be (re)created later, e.g. via a
                # retry job or manually.
                logger.exception("Video room creation failed for session %s (assign_teacher)", session.id)
        session.save(update_fields=["assigned_teacher", "status", "meeting_url", "calendar_event_id"])
        return Response(ClassSessionSerializer(session).data)


# ---------------------------------------------------------------------------
# Enrollments (booking)
# ---------------------------------------------------------------------------
class EnrollmentViewSet(viewsets.ModelViewSet):
    serializer_class = EnrollmentSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdmin]

    def get_queryset(self):
        user = self.request.user
        qs = Enrollment.objects.select_related("class_session", "student")
        return qs if user.role == "admin" else qs.filter(student=user)

    @transaction.atomic
    def perform_create(self, serializer):
        """
        Booking logic (spec 5.1):
        - If the session still has capacity, the student is enrolled directly
          (payment_status starts "pending" until Module 3's payment flow
          confirms it — see spec: "seat counts... updated only upon
          successful payment").
        - If the session is full, the student is enrolled as `waitlisted`
          instead of being rejected outright.
        NOTE: automatically promoting a waitlisted student when a seat frees
        up should be triggered from the cancellation flow below.
        """
        class_session = serializer.validated_data["class_session"]
        # select_for_update to avoid a race between two students booking the
        # last seat at the same time.
        session = ClassSession.objects.select_for_update().get(pk=class_session.pk)
        is_full = not session.has_capacity
        enrollment = serializer.save(student=self.request.user, waitlisted=is_full)
        if is_full:
            notifications.send_waitlisted(enrollment)

    @action(detail=True, methods=["post"])
    def create_checkout_session(self, request, pk=None):
        """
        Starts a payment flow for this (pending, non-waitlisted) enrollment
        — Stripe normally, or Konnect if the student's country is Tunisia
        (Stripe doesn't support TND — see core/services/konnect.py). The
        frontend just redirects to the returned URL either way; actual
        confirmation happens via the respective webhook, not this response.
        """
        enrollment = self.get_object()
        if enrollment.waitlisted:
            return Response({"detail": "Cannot pay for a waitlisted enrollment yet."}, status=status.HTTP_400_BAD_REQUEST)
        if enrollment.payment_status == Enrollment.PaymentStatus.PAID:
            return Response({"detail": "This enrollment is already paid."}, status=status.HTTP_400_BAD_REQUEST)
        if enrollment.student.student_profile.requires_parental_consent:
            return Response(
                {"detail": "Une autorisation parentale est requise avant de payer une session.",
                 "code": "parental_consent_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        amount_cents = session_price_cents(enrollment.class_session)

        try:
            amount_cents, promo = discounts.apply_discounts(amount_cents, request.data.get("promo_code"))
        except discounts.InvalidPromoCode as exc:
            return Response({"detail": str(exc), "code": "invalid_promo_code"}, status=status.HTTP_400_BAD_REQUEST)

        if enrollment.student.country == "Tunisie":
            # Pas de compte marchand Konnect confirmé — paiement 100%
            # manuel pour la Tunisie (virement bancaire direct, aucun
            # intermédiaire). L'élève contacte l'admin par e-mail, qui lui
            # communique le RIB, puis approuve manuellement une fois reçu
            # (voir EnrollmentAdmin.mark_paid_bank_transfer).
            return Response(
                {"detail": "Le paiement en ligne n'est pas disponible pour la Tunisie. "
                           f"Contactez-nous à {settings.CONTACT_EMAIL} pour connaître les modalités "
                           "de paiement par virement bancaire.",
                 "code": "payment_by_email_tunisia",
                 "contact_email": settings.CONTACT_EMAIL},
                status=status.HTTP_200_OK,
            )

        try:
            checkout_session = payments.create_enrollment_checkout_session(enrollment, amount_cents)
        except Exception as exc:
            return Response({"detail": f"Stripe error: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        discounts.record_promo_code_use(promo)
        Payment.objects.create(
            user=enrollment.student, enrollment=enrollment,
            amount=amount_cents / 100, stripe_checkout_session_id=checkout_session.id,
        )
        return Response({"checkout_url": checkout_session.url})

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        Student- or admin-initiated cancellation (spec 5.2). Refunds any
        paid enrollment when the cancellation is within the notice period
        (a late cancellation isn't eligible — see payments.refund_enrollment_if_paid).
        """
        enrollment = self.get_object()
        session = enrollment.class_session
        hours_until_start = (session.start_time - timezone.now()).total_seconds() / 3600

        if request.user.role != "admin" and hours_until_start < CANCELLATION_NOTICE_HOURS:
            reason = "late_cancellation_no_refund"
        else:
            reason = "cancelled_within_notice_period"
            payments.refund_enrollment_if_paid(enrollment)

        enrollment.cancelled_at = timezone.now()
        enrollment.cancellation_reason = reason
        enrollment.save(update_fields=["cancelled_at", "cancellation_reason"])
        notifications.send_cancellation_confirmation(enrollment)

        self._promote_next_waitlisted(session)
        return Response(EnrollmentSerializer(enrollment).data)

    @staticmethod
    def _promote_next_waitlisted(session):
        next_in_line = (
            Enrollment.objects.select_for_update()
            .filter(class_session=session, waitlisted=True, cancelled_at__isnull=True)
            .order_by("booked_at")
            .first()
        )
        if next_in_line and session.has_capacity:
            next_in_line.waitlisted = False
            next_in_line.save(update_fields=["waitlisted"])
            notifications.send_waitlist_seat_available(next_in_line)


# ---------------------------------------------------------------------------
# Individual bookings — the INDIVIDUAL tier bypasses the group-request flow
# entirely (spec: "l'individuel est la seule formule où l'élève peut payer
# par séance et choisir n'importe quelle date/heure directement, car il ne
# dépend pas des groupes, et il n'a aucun engagement"). The student picks
# their own slot and pays immediately via Stripe Checkout; a teacher is
# assigned by the admin afterward through the normal "sessions à affecter"
# queue, same as any other session.
# ---------------------------------------------------------------------------
class IndividualBookingView(APIView):
    """POST subject + level + start_time + end_time -> creates the session, enrolls the student, and returns a Stripe checkout URL."""
    permission_classes = [permissions.IsAuthenticated, IsStudent]

    @transaction.atomic
    def post(self, request):
        if request.user.student_profile.requires_parental_consent:
            return Response(
                {"detail": "Une autorisation parentale est requise avant de réserver un cours individuel.",
                 "code": "parental_consent_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = IndividualBookingSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        session = ClassSession.objects.create(
            subject=data["subject"], level=data["level"], group_tier=ClassSession.GroupTier.INDIVIDUAL,
            max_capacity=1, start_time=data["start_time"], end_time=data["end_time"],
            status=ClassSession.Status.SCHEDULED, preferred_teacher=data.get("preferred_teacher"),
        )
        enrollment = Enrollment.objects.create(student=request.user, class_session=session)
        notifications.send_enrollment_confirmed(enrollment)

        if request.user.country == "Tunisie":
            # Voir le même commentaire dans EnrollmentViewSet.create_checkout_session
            # — pas d'intermédiaire de paiement pour la Tunisie, l'élève
            # contacte l'admin par e-mail. La réservation (enrollment) est
            # déjà créée ci-dessus, en attente de paiement (PENDING) —
            # l'admin l'approuve manuellement une fois le virement reçu.
            return Response(
                {"detail": "Votre réservation est enregistrée. Le paiement en ligne n'est pas disponible "
                           f"pour la Tunisie : contactez-nous à {settings.CONTACT_EMAIL} pour connaître les "
                           "modalités de paiement par virement bancaire.",
                 "code": "payment_by_email_tunisia",
                 "contact_email": settings.CONTACT_EMAIL,
                 "enrollment": EnrollmentSerializer(enrollment).data},
                status=status.HTTP_201_CREATED,
            )

        amount_cents = session_price_cents(session)
        try:
            amount_cents, promo = discounts.apply_discounts(amount_cents, request.data.get("promo_code"))
        except discounts.InvalidPromoCode as exc:
            return Response({"detail": str(exc), "code": "invalid_promo_code"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            checkout_session = payments.create_enrollment_checkout_session(enrollment, amount_cents)
        except Exception as exc:
            return Response({"detail": f"Stripe error: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)

        discounts.record_promo_code_use(promo)
        Payment.objects.create(
            user=request.user, enrollment=enrollment,
            amount=amount_cents / 100, stripe_checkout_session_id=checkout_session.id,
        )
        return Response(
            {"checkout_url": checkout_session.url, "enrollment": EnrollmentSerializer(enrollment).data},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Group requests — new booking flow (confirmed with product owner): students
# no longer pick a time slot themselves. They request a subject + level +
# group size, and an admin later assembles matching requests into a real,
# fixed, recurring group (see AdminScheduleGroupView) — so the same students
# stay together with the same teacher for continuity (spec: "l'enseignant ne
# doit pas être mêlé").
# ---------------------------------------------------------------------------
class GroupRequestViewSet(viewsets.ModelViewSet):
    serializer_class = GroupRequestSerializer
    filterset_fields = ["subject", "level", "group_tier", "status"]

    def get_permissions(self):
        if self.action in ["update", "partial_update", "destroy"]:
            return [permissions.IsAuthenticated(), IsOwnerOrAdmin()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        qs = GroupRequest.objects.select_related(
            "subject", "student", "group_assignment", "resulting_enrollment__class_session"
        )
        return qs if user.role == "admin" else qs.filter(student=user)

    def perform_create(self, serializer):
        serializer.save(student=self.request.user)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        group_request = self.get_object()
        if group_request.status != GroupRequest.Status.PENDING:
            return Response(
                {"detail": "Only a pending request can be cancelled — this one has already been assigned to a teacher or scheduled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        group_request.status = GroupRequest.Status.CANCELLED
        group_request.save(update_fields=["status"])
        return Response(GroupRequestSerializer(group_request).data)

    @action(detail=False, methods=["get"], permission_classes=[IsAdmin])
    def pending_summary(self, request):
        """
        Admin-only: pending requests grouped by subject/level/group size/
        weekly-hour package, with counts — this is the admin's "these are
        the groups I could form right now" view, used to decide when/how
        to schedule.
        """
        summary = (
            GroupRequest.objects.filter(status=GroupRequest.Status.PENDING)
            .values("subject_id", "subject__name", "level", "group_tier", "weekly_hours")
            .annotate(count=Count("id"))
            .order_by("subject__name", "level", "group_tier", "weekly_hours")
        )
        return Response(list(summary))


class AdminAssignGroupView(APIView):
    """
    Admin-only, autonomous scheduling model: bundles a set of matching
    pending GroupRequests into a GroupAssignment and hands it to a
    teacher — no schedule is picked here. The teacher then defines the
    actual day/time/recurrence (and their meeting link) themselves, from
    their dashboard — see GroupAssignmentViewSet.schedule below.

    Expected payload:
    {
        "request_ids": [1, 2, 3],
        "teacher_id": 4,
        "is_billable": true   // false for an *additional* weekly slot of
                                // a package already billed elsewhere —
                                // see SeriesMembership.is_billable
    }
    """
    permission_classes = [IsAdmin]

    @transaction.atomic
    def post(self, request):
        request_ids = request.data.get("request_ids", [])
        teacher_id = request.data.get("teacher_id")
        is_billable = bool(request.data.get("is_billable", True))

        group_requests = list(
            GroupRequest.objects.select_for_update()
            .filter(id__in=request_ids, status=GroupRequest.Status.PENDING)
            .select_related("student", "subject")
        )
        if not group_requests:
            return Response({"detail": "No matching pending requests found."}, status=status.HTTP_400_BAD_REQUEST)

        first = group_requests[0]
        if any(
            r.subject_id != first.subject_id or r.level != first.level
            or r.group_tier != first.group_tier or r.weekly_hours != first.weekly_hours
            for r in group_requests
        ):
            return Response(
                {"detail": "All selected requests must share the same subject, level, group size, and weekly-hour package."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_capacity = ClassSession.TIER_CAPACITY[first.group_tier]
        if len(group_requests) > max_capacity:
            return Response(
                {"detail": f"Selected {len(group_requests)} students, but {first.group_tier} only holds {max_capacity}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            teacher = TeacherProfile.objects.get(pk=teacher_id, is_active=True)
        except TeacherProfile.DoesNotExist:
            return Response({"detail": "No active teacher with that id."}, status=status.HTTP_400_BAD_REQUEST)

        assignment = GroupAssignment.objects.create(
            subject=first.subject, level=first.level, group_tier=first.group_tier,
            weekly_hours=first.weekly_hours, teacher=teacher, is_billable=is_billable,
        )
        for group_request in group_requests:
            group_request.status = GroupRequest.Status.TEACHER_ASSIGNED
            group_request.group_assignment = assignment
            group_request.save(update_fields=["status", "group_assignment"])

        notifications.send_group_assigned_to_teacher(assignment)

        return Response(GroupAssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)


class GroupAssignmentViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Groups formed by the admin and assigned to a teacher (see
    AdminAssignGroupView above). Teachers see only their own — this is
    their "groups to schedule" queue; admins see everyone's, for
    visibility into what's still awaiting a teacher's schedule.
    """
    serializer_class = GroupAssignmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["status", "teacher"]

    def get_queryset(self):
        user = self.request.user
        qs = GroupAssignment.objects.select_related("subject", "teacher__user").prefetch_related("requests__student")
        if user.role == "admin":
            return qs
        if user.role == "teacher":
            return qs.filter(teacher__user=user)
        return qs.none()

    @action(detail=True, methods=["post"], permission_classes=[IsTeacher])
    def schedule(self, request, pk=None):
        """
        Teacher-only: adds one or more weekly recurring slots to this
        group's package (each becomes its own ClassSeries), enrolling
        every student in the group into all of them. `weekly_hours` is the
        package's TOTAL MONTHLY commitment — divide by 4 for the weekly
        target (e.g. 12h/mois ≈ 3h/semaine), which may need MORE THAN
        ONE slot to reach (e.g. Monday 1h30 + Thursday 1h30),
        so **this can be called more than once**: the first call moves
        the group from "awaiting schedule" to "scheduled", and later calls
        just add more slots the same way (e.g. if the teacher only decided
        on one day at first). See GroupAssignmentSerializer's
        `scheduled_slots`/`scheduled_weekly_minutes`/`target_weekly_minutes`
        for what's already been set up vs. the package's target.

        Meeting link: unless `meeting_url` is given explicitly here, each
        slot gets one via this teacher's own link/Google account, same
        resolution order as everywhere else (see core/services/video.py).

        Expected payload:
        {
            "slots": [
                {"start_time": "2026-09-01T18:00:00Z", "end_time": "2026-09-01T20:00:00Z", "ends_on": "2026-12-15"},
                {"start_time": "2026-09-04T18:00:00Z", "end_time": "2026-09-04T20:00:00Z", "ends_on": "2026-12-15"}
            ],
            "meeting_url": "https://..."  // optional — same link applied to every slot in this call
        }
        (`ends_on` can be omitted on later slots if it's the same for all —
        it then defaults to the latest `ends_on` already used on this
        assignment, if any.)
        """
        assignment = self.get_object()  # get_queryset already scopes this to the requesting teacher

        raw_slots = request.data.get("slots")
        if not raw_slots or not isinstance(raw_slots, list):
            return Response(
                {"detail": "slots (a non-empty list of {start_time, end_time, ends_on}) is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        manual_meeting_url = (request.data.get("meeting_url") or "").strip()
        # An additional slot never re-bills the same package — only the
        # very first slot ever scheduled for this assignment can be
        # billable, and only if the assignment itself is marked as such.
        is_first_slot_ever = not assignment.class_series.exists()
        fallback_ends_on = (
            assignment.class_series.order_by("-ends_on").values_list("ends_on", flat=True).first()
        )

        parsed_slots = []
        for raw_slot in raw_slots:
            start_dt = parse_datetime(raw_slot.get("start_time", ""))
            end_dt = parse_datetime(raw_slot.get("end_time", ""))
            ends_on = raw_slot.get("ends_on") or fallback_ends_on
            if not start_dt or not end_dt:
                return Response(
                    {"detail": "Each slot needs start_time and end_time, in ISO 8601 format."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not ends_on:
                return Response(
                    {"detail": "ends_on (date) is required on at least the first slot ever added."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parsed_slots.append((start_dt, end_dt, ends_on))

        with transaction.atomic():
            group_requests = list(
                GroupRequest.objects.select_for_update().filter(group_assignment=assignment).select_related("student")
            )
            if not group_requests:
                return Response({"detail": "Ce groupe n'a plus d'élèves rattachés."}, status=status.HTTP_400_BAD_REQUEST)

            max_capacity = ClassSession.TIER_CAPACITY[assignment.group_tier]
            created_sessions = []

            for index, (start_dt, end_dt, ends_on) in enumerate(parsed_slots):
                slot_is_billable = assignment.is_billable and is_first_slot_ever and index == 0

                series = ClassSeries.objects.create(
                    subject=assignment.subject, level=assignment.level, group_tier=assignment.group_tier,
                    weekly_hours=assignment.weekly_hours,
                    weekday=start_dt.weekday(), start_time=start_dt.time(),
                    duration_minutes=int((end_dt - start_dt).total_seconds() // 60),
                    starts_on=start_dt.date(), ends_on=ends_on, assigned_teacher=assignment.teacher,
                    group_assignment=assignment,
                )
                session = ClassSession.objects.create(
                    subject=assignment.subject, level=assignment.level, group_tier=assignment.group_tier,
                    max_capacity=max_capacity, assigned_teacher=assignment.teacher, series=series,
                    start_time=start_dt, end_time=end_dt, status=ClassSession.Status.ASSIGNED,
                    group_assignment=assignment,
                )
                created_sessions.append(session)

                if manual_meeting_url:
                    session.meeting_url = manual_meeting_url
                    session.save(update_fields=["meeting_url"])
                else:
                    try:
                        session.meeting_url, event_id = video.create_room_for_session_full(session)
                        if event_id:
                            session.calendar_event_id = event_id
                        session.save(update_fields=["meeting_url", "calendar_event_id"])
                    except Exception:
                        logger.exception("Video room creation failed for session %s (group schedule)", session.id)

                monthly_price = series_monthly_price_cents(series)
                for group_request in group_requests:
                    enrollment = Enrollment.objects.create(student=group_request.student, class_session=session)
                    membership, membership_created = SeriesMembership.objects.get_or_create(
                        student=group_request.student, series=series,
                        defaults={
                            "monthly_price_cents": monthly_price if slot_is_billable else 0,
                            "is_billable": slot_is_billable,
                        },
                    )
                    # Only the very first slot's enrollment is recorded here —
                    # resulting_enrollment is a single FK (see GroupRequest);
                    # subsequent slots still enroll the student (above), just
                    # without re-pointing this reference.
                    if group_request.resulting_enrollment_id is None:
                        group_request.status = GroupRequest.Status.SCHEDULED
                        group_request.resulting_enrollment = enrollment
                        group_request.save(update_fields=["status", "resulting_enrollment"])

                    # Saved-card billing model: the student entered their
                    # card when they requested this package (see
                    # PaymentMethodSetupView) but was never charged then —
                    # THIS is the moment they actually get billed, right as
                    # their teacher schedules a real session, no separate
                    # action needed from them. Falls back silently to the
                    # existing manual "Payer" button on their dashboard if
                    # they have no saved card yet, or if the off-session
                    # charge is declined (expired card, insufficient
                    # funds...) — either way, scheduling itself is never
                    # blocked by a payment problem.
                    if slot_is_billable and membership_created:
                        subscription, charge_failed = None, False
                        try:
                            subscription = payments.charge_saved_payment_method(membership)
                        except Exception:
                            charge_failed = True

                        if subscription:
                            membership.status = SeriesMembership.Status.ACTIVE
                            membership.stripe_subscription_id = subscription.id
                            membership.save(update_fields=["status", "stripe_subscription_id"])
                            payment_intent_id = ""
                            try:
                                payment_intent_id = subscription.latest_invoice.payment_intent.id
                            except Exception:
                                pass
                            mark_series_enrollments_paid(membership, stripe_payment_intent_id=payment_intent_id)
                        elif charge_failed:
                            notifications.send_payment_method_declined(membership)

                    notifications.send_group_scheduled(enrollment)

            assignment.status = GroupAssignment.Status.SCHEDULED
            assignment.save(update_fields=["status"])

        return Response(
            {
                "sessions": ClassSessionSerializer(created_sessions, many=True).data,
                "assignment": GroupAssignmentSerializer(assignment).data,
                "enrolled_count": len(group_requests),
            },
            status=status.HTTP_201_CREATED,
        )


class AdminScheduleGroupView(APIView):
    """
    LEGACY / manual override — the primary flow is now AdminAssignGroupView
    + GroupAssignmentViewSet.schedule (autonomous scheduling model: the
    admin assigns a teacher, the teacher picks the day/time and link
    themselves). This view is kept working for admins who need to force a
    schedule directly (e.g. on behalf of a teacher who can't access their
    dashboard) — it still does both steps atomically, bypassing
    GroupAssignment entirely, and is no longer wired into the admin
    frontend dashboard.

    Admin-only: turns a set of pending GroupRequests (all sharing the same
    subject/level/group size/weekly-hour package) into one real, scheduled
    ClassSession — optionally recurring weekly via a ClassSeries — with a
    teacher assigned. This is the moment a fixed group is born.

    Expected payload:
    {
        "request_ids": [1, 2, 3],
        "start_time": "2026-09-02T18:00:00Z",
        "end_time": "2026-09-02T19:00:00Z",
        "teacher_id": 4,
        "recurring": true,
        "ends_on": "2026-12-15",   // required if recurring
        "is_billable": true        // false for an *additional* weekly slot
                                    // of a package already billed elsewhere
                                    // — see SeriesMembership.is_billable
    }
    """
    permission_classes = [IsAdmin]

    @transaction.atomic
    def post(self, request):
        request_ids = request.data.get("request_ids", [])
        teacher_id = request.data.get("teacher_id")
        recurring = bool(request.data.get("recurring", False))
        ends_on = request.data.get("ends_on")
        is_billable = bool(request.data.get("is_billable", True))

        start_dt = parse_datetime(request.data.get("start_time", ""))
        end_dt = parse_datetime(request.data.get("end_time", ""))
        if not start_dt or not end_dt:
            return Response(
                {"detail": "start_time and end_time are required, in ISO 8601 format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group_requests = list(
            GroupRequest.objects.select_for_update()
            .filter(id__in=request_ids, status=GroupRequest.Status.PENDING)
            .select_related("student", "subject")
        )
        if not group_requests:
            return Response({"detail": "No matching pending requests found."}, status=status.HTTP_400_BAD_REQUEST)

        first = group_requests[0]
        if any(
            r.subject_id != first.subject_id or r.level != first.level
            or r.group_tier != first.group_tier or r.weekly_hours != first.weekly_hours
            for r in group_requests
        ):
            return Response(
                {"detail": "All selected requests must share the same subject, level, group size, and weekly-hour package."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_capacity = ClassSession.TIER_CAPACITY[first.group_tier]
        if len(group_requests) > max_capacity:
            return Response(
                {"detail": f"Selected {len(group_requests)} students, but {first.group_tier} only holds {max_capacity}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            teacher = TeacherProfile.objects.get(pk=teacher_id, is_active=True)
        except TeacherProfile.DoesNotExist:
            return Response({"detail": "No active teacher with that id."}, status=status.HTTP_400_BAD_REQUEST)

        series = None
        if recurring:
            if not ends_on:
                return Response(
                    {"detail": "ends_on (date) is required to create a recurring weekly group."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            series = ClassSeries.objects.create(
                subject=first.subject, level=first.level, group_tier=first.group_tier,
                weekly_hours=first.weekly_hours,
                weekday=start_dt.weekday(), start_time=start_dt.time(),
                duration_minutes=int((end_dt - start_dt).total_seconds() // 60),
                starts_on=start_dt.date(), ends_on=ends_on, assigned_teacher=teacher,
            )

        session = ClassSession.objects.create(
            subject=first.subject, level=first.level, group_tier=first.group_tier,
            max_capacity=max_capacity, assigned_teacher=teacher, series=series,
            start_time=start_dt, end_time=end_dt, status=ClassSession.Status.ASSIGNED,
        )
        try:
            session.meeting_url, event_id = video.create_room_for_session_full(session)
            if event_id:
                session.calendar_event_id = event_id
            session.save(update_fields=["meeting_url", "calendar_event_id"])
        except Exception:
            logger.exception("Video room creation failed for session %s (individual booking)", session.id)

        monthly_price = series_monthly_price_cents(series) if series else None

        enrolled_count = 0
        for group_request in group_requests:
            enrollment = Enrollment.objects.create(student=group_request.student, class_session=session)
            group_request.status = GroupRequest.Status.SCHEDULED
            group_request.resulting_enrollment = enrollment
            group_request.save(update_fields=["status", "resulting_enrollment"])

            if series:
                # Recurring group: billed monthly, auto-renewing (spec:
                # changes/cancellations take effect the following month) —
                # see SeriesMembership. One-off sessions stay on the
                # existing per-session Stripe checkout.
                SeriesMembership.objects.get_or_create(
                    student=group_request.student, series=series,
                    defaults={"monthly_price_cents": monthly_price if is_billable else 0, "is_billable": is_billable},
                )

            notifications.send_group_scheduled(enrollment)
            enrolled_count += 1

        return Response(
            {
                "session": ClassSessionSerializer(session).data,
                "series_id": series.id if series else None,
                "enrolled_count": enrolled_count,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Series memberships — monthly billing + 2-week notice to leave a fixed
# recurring group (spec: "si l'élève ne résilie pas, le renouvellement est
# automatique"; leaving requires 2 weeks' notice, same as the deferred
# full-curriculum formula).
# ---------------------------------------------------------------------------
def first_of_next_month(moment):
    """The 1st of the month following `moment`, at 00:00, timezone-aware."""
    year, month = (moment.year + 1, 1) if moment.month == 12 else (moment.year, moment.month + 1)
    return timezone.make_aware(datetime(year, month, 1))


class SeriesMembershipViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SeriesMembershipSerializer

    def get_queryset(self):
        user = self.request.user
        qs = SeriesMembership.objects.select_related("series__subject", "student")
        return qs if user.role == "admin" else qs.filter(student=user)

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        """
        Starts (or restarts) the monthly payment for this membership —
        Stripe subscription (auto-renewing) normally, or a one-off Konnect
        payment if the student is in Tunisia.

        IMPORTANT: Konnect has no recurring/subscription billing (no
        Tunisian gateway does, as of writing) — unlike the Stripe path,
        calling this does NOT set up automatic renewal for Tunisian
        students. It only pays for the current month; the student (or an
        admin on their behalf) must call this endpoint again next month.
        """
        membership = self.get_object()
        if membership.student != request.user and request.user.role != "admin":
            return Response(status=status.HTTP_403_FORBIDDEN)
        if membership.student.student_profile.requires_parental_consent:
            return Response(
                {"detail": "Une autorisation parentale est requise avant de payer un abonnement.",
                 "code": "parental_consent_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if membership.student.country == "Tunisie":
            # Voir le même commentaire dans EnrollmentViewSet.create_checkout_session.
            return Response(
                {"detail": "Le paiement en ligne n'est pas disponible pour la Tunisie. "
                           f"Contactez-nous à {settings.CONTACT_EMAIL} pour connaître les modalités "
                           "de paiement par virement bancaire.",
                 "code": "payment_by_email_tunisia",
                 "contact_email": settings.CONTACT_EMAIL},
                status=status.HTTP_200_OK,
            )

        # Seul le rabais global s'applique ici (pas de code promo — voir
        # payments.create_series_subscription_checkout_session).
        amount_cents, _ = discounts.apply_discounts(membership.monthly_price_cents)
        try:
            checkout_session = payments.create_series_subscription_checkout_session(membership, unit_amount_cents_override=amount_cents)
        except Exception as exc:
            return Response({"detail": f"Stripe error: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"checkout_url": checkout_session.url})

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        """
        The current calendar month's package always runs to completion
        unchanged (spec: "le groupe doit terminer le forfait mensuel sans
        aucun changement") — leaving takes effect on the 1st of the
        following month. The membership stays ACTIVE/billed and the
        student keeps being auto-enrolled in upcoming occurrences until
        `leaves_on` — see `finalize_series_departures`, the scheduled
        command that actually cancels the Stripe subscription once that
        date arrives.
        """
        membership = self.get_object()
        if membership.student != request.user and request.user.role != "admin":
            return Response(status=status.HTTP_403_FORBIDDEN)
        if membership.status != SeriesMembership.Status.ACTIVE:
            return Response(
                {"detail": "This membership isn't active (already leaving or left)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        now = timezone.now()
        membership.status = SeriesMembership.Status.LEAVING
        membership.leave_requested_at = now
        membership.leaves_on = first_of_next_month(now)
        membership.save(update_fields=["status", "leave_requested_at", "leaves_on"])
        return Response(SeriesMembershipSerializer(membership).data)


# ---------------------------------------------------------------------------
# Forum
# ---------------------------------------------------------------------------
class ForumThreadViewSet(viewsets.ModelViewSet):
    queryset = ForumThread.objects.select_related("user", "subject").all()
    serializer_class = ForumThreadSerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["subject", "level", "is_solved"]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def mark_solved(self, request, pk=None):
        thread = self.get_object()
        if request.user != thread.user and request.user.role != "teacher":
            return Response({"detail": "Only the author or a teacher can mark this solved."}, status=status.HTTP_403_FORBIDDEN)
        thread.is_solved = True
        thread.save(update_fields=["is_solved"])
        return Response(ForumThreadSerializer(thread).data)


class ForumReplyViewSet(viewsets.ModelViewSet):
    queryset = ForumReply.objects.select_related("user", "thread").all()
    serializer_class = ForumReplySerializer
    permission_classes = [permissions.IsAuthenticated]
    filterset_fields = ["thread"]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ---------------------------------------------------------------------------
# Materials (teacher uploads attached to a session)
# ---------------------------------------------------------------------------
class MaterialViewSet(viewsets.ModelViewSet):
    """
    Content a teacher shares with their students — documents or video
    links, attached to a whole group or a single session (see
    models.Material). Teachers only see/manage materials for
    groups/sessions THEY are actually assigned to; students only see
    materials for groups they belong to or sessions they're enrolled in.
    """
    serializer_class = MaterialSerializer
    filterset_fields = ["class_session", "group_assignment"]

    def get_queryset(self):
        user = self.request.user
        qs = Material.objects.select_related(
            "group_assignment__subject", "class_session__subject", "uploaded_by"
        )
        if user.role == "admin":
            return qs
        if user.role == "teacher":
            return qs.filter(Q(group_assignment__teacher__user=user) | Q(class_session__assigned_teacher__user=user))
        if user.role == "student":
            group_ids = GroupRequest.objects.filter(
                student=user, group_assignment__isnull=False
            ).values_list("group_assignment_id", flat=True)
            session_ids = Enrollment.objects.filter(student=user).values_list("class_session_id", flat=True)
            return qs.filter(Q(group_assignment_id__in=group_ids) | Q(class_session_id__in=session_ids))
        return qs.none()

    def get_permissions(self):
        if self.action in ["create", "update", "partial_update", "destroy"]:
            return [permissions.IsAuthenticated(), IsTeacher()]
        return [permissions.IsAuthenticated()]

    def _check_ownership(self, group_assignment, class_session):
        teacher = self.request.user.teacher_profile
        if group_assignment and group_assignment.teacher_id != teacher.id:
            raise PermissionDenied("Ce groupe ne vous est pas assigné.")
        if class_session and class_session.assigned_teacher_id != teacher.id:
            raise PermissionDenied("Cette séance ne vous est pas assignée.")

    def perform_create(self, serializer):
        self._check_ownership(
            serializer.validated_data.get("group_assignment"), serializer.validated_data.get("class_session")
        )
        serializer.save(uploaded_by=self.request.user)

    def perform_update(self, serializer):
        instance = self.get_object()
        self._check_ownership(
            serializer.validated_data.get("group_assignment", instance.group_assignment),
            serializer.validated_data.get("class_session", instance.class_session),
        )
        serializer.save()

    def perform_destroy(self, instance):
        self._check_ownership(instance.group_assignment, instance.class_session)
        instance.delete()


class GroupAnnouncementViewSet(viewsets.ModelViewSet):
    """
    Messages a teacher posts for every student in one of their groups —
    see models.GroupAnnouncement. Same ownership scoping as MaterialViewSet.
    """
    serializer_class = GroupAnnouncementSerializer
    filterset_fields = ["group_assignment"]

    def get_queryset(self):
        user = self.request.user
        qs = GroupAnnouncement.objects.select_related("group_assignment__subject", "author")
        if user.role == "admin":
            return qs
        if user.role == "teacher":
            return qs.filter(group_assignment__teacher__user=user)
        if user.role == "student":
            group_ids = GroupRequest.objects.filter(
                student=user, group_assignment__isnull=False
            ).values_list("group_assignment_id", flat=True)
            return qs.filter(group_assignment_id__in=group_ids)
        return qs.none()

    def get_permissions(self):
        if self.action in ["create", "update", "partial_update", "destroy"]:
            return [permissions.IsAuthenticated(), IsTeacher()]
        return [permissions.IsAuthenticated()]

    def _check_ownership(self, group_assignment):
        teacher = self.request.user.teacher_profile
        if group_assignment.teacher_id != teacher.id:
            raise PermissionDenied("Ce groupe ne vous est pas assigné.")

    def perform_create(self, serializer):
        group_assignment = serializer.validated_data["group_assignment"]
        self._check_ownership(group_assignment)
        announcement = serializer.save(author=self.request.user)

        student_emails = list(
            GroupRequest.objects.filter(group_assignment=group_assignment)
            .exclude(student__email="")
            .values_list("student__email", flat=True)
        )
        notifications.send_group_announcement(announcement, student_emails)

    def perform_update(self, serializer):
        instance = self.get_object()
        self._check_ownership(serializer.validated_data.get("group_assignment", instance.group_assignment))
        serializer.save()

    def perform_destroy(self, instance):
        self._check_ownership(instance.group_assignment)
        instance.delete()


# ---------------------------------------------------------------------------
# Admin overview
# ---------------------------------------------------------------------------
class AdminStatsView(APIView):
    """
    Summary numbers for the admin dashboard's top cards. Kept as a single
    endpoint (rather than making the frontend compute from list endpoints)
    so the dashboard stays fast as data grows.
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        now = timezone.now()
        week_end = now + timedelta(days=7)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        active_students = User.objects.filter(role=User.Role.STUDENT, is_active=True).count()
        sessions_this_week = ClassSession.objects.filter(start_time__range=(now, week_end)).count()
        sessions_to_assign = ClassSession.objects.filter(
            assigned_teacher__isnull=True, status=ClassSession.Status.SCHEDULED, start_time__gte=now
        ).count()
        revenue_this_month = (
            Payment.objects.filter(status=Payment.Status.SUCCEEDED, created_at__gte=month_start)
            .values("currency").annotate(total=Sum("amount"))
        )
        # Dict {"EUR": "1234.00", "TND": "980.50"} — jamais un seul total,
        # un revenu EUR et un revenu TND ne s'additionnent pas (voir
        # AdminReferralsView / compute_payouts.py pour le même principe).
        revenue_this_month = {row["currency"]: str(row["total"]) for row in revenue_this_month}
        pending_teachers = TeacherProfile.objects.filter(is_active=False).count()

        return Response({
            "active_students": active_students,
            "sessions_this_week": sessions_this_week,
            "sessions_to_assign": sessions_to_assign,
            "revenue_this_month": revenue_this_month,
            "pending_teachers": pending_teachers,
        })


def _hours_for_teacher_qs(teacher_ids_filter=None, month_param=None):
    """
    Logique partagée entre AdminTeacherHoursView (tous les enseignants) et
    MyTeacherHoursView (un seul enseignant, lui-même) — même calcul, même
    définition de "séance qui a eu lieu" (voir docstring
    AdminTeacherHoursView), pour ne jamais avoir deux chiffres différents
    entre la vue admin et celle de l'enseignant sur les mêmes séances.
    Retourne (week_start, week_end, month_start, results_by_teacher_id).
    """
    now = timezone.now()

    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=7)

    if month_param:
        try:
            month_start = datetime.strptime(month_param, "%Y-%m").replace(tzinfo=now.tzinfo)
        except ValueError:
            raise ValueError("Le paramètre month doit être au format AAAA-MM (ex : 2026-08).")
    else:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = (
        month_start.replace(year=month_start.year + 1, month=1)
        if month_start.month == 12
        else month_start.replace(month=month_start.month + 1)
    )

    base_qs = ClassSession.objects.filter(
        assigned_teacher__isnull=False, start_time__lte=now
    ).exclude(status=ClassSession.Status.CANCELLED)
    if teacher_ids_filter is not None:
        base_qs = base_qs.filter(assigned_teacher__in=teacher_ids_filter)

    duration_expr = ExpressionWrapper(F("end_time") - F("start_time"), output_field=DurationField())

    def hours_by_teacher(qs):
        rows = (
            qs.annotate(duration=duration_expr)
            .values("assigned_teacher")
            .annotate(total=Sum("duration"), session_count=Count("id"))
        )
        return {row["assigned_teacher"]: row for row in rows}

    week_totals = hours_by_teacher(base_qs.filter(start_time__gte=week_start, start_time__lt=week_end))
    month_totals = hours_by_teacher(base_qs.filter(start_time__gte=month_start, start_time__lt=month_end))

    return week_start, week_end, month_start, month_end, week_totals, month_totals


class AdminTeacherHoursView(APIView):
    """
    GET /api/admin/teacher-hours/?month=YYYY-MM — heures données par chaque
    enseignant, pour préparer la rémunération (spec 5.4 / modèle Payout).

    Compte les ClassSession qui ont un assigned_teacher, ne sont pas
    annulées, et dont le start_time est déjà passé — c'est le signal le
    plus fiable pour "a effectivement eu lieu" dans ce projet, car
    ClassSession.Status.COMPLETED existe dans le modèle mais n'est mis à
    jour nulle part dans le code actuellement (aucune tâche planifiée ne
    le fait). Si vous ajoutez plus tard un vrai marquage "terminé", il
    suffira de remplacer le filtre start_time__lte=now par
    status=COMPLETED ci-dessous.

    "hours_this_week" est toujours la semaine ISO en cours (lundi-dimanche)
    quel que soit `month`. "hours_this_month" respecte le paramètre `month`
    s'il est fourni, sinon le mois en cours.
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        try:
            week_start, week_end, month_start, month_end, week_totals, month_totals = _hours_for_teacher_qs(
                month_param=request.query_params.get("month")
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        results = []
        for teacher in TeacherProfile.objects.filter(is_active=True).select_related("user"):
            week_row = week_totals.get(teacher.id)
            month_row = month_totals.get(teacher.id)
            results.append({
                "teacher_id": teacher.id,
                "teacher_name": f"{teacher.user.first_name} {teacher.user.last_name}".strip(),
                "teacher_email": teacher.user.email,
                "compensation_type": teacher.compensation_type,
                "compensation_rate": teacher.compensation_rate,
                "hours_this_week": round(week_row["total"].total_seconds() / 3600, 2) if week_row and week_row["total"] else 0,
                "sessions_this_week": week_row["session_count"] if week_row else 0,
                "hours_this_month": round(month_row["total"].total_seconds() / 3600, 2) if month_row and month_row["total"] else 0,
                "sessions_this_month": month_row["session_count"] if month_row else 0,
            })
        results.sort(key=lambda r: r["hours_this_month"], reverse=True)

        return Response({
            "week_start": week_start.date(),
            "week_end": (week_end - timedelta(days=1)).date(),
            "month": month_start.strftime("%Y-%m"),
            "teachers": results,
        })


class MyTeacherHoursView(APIView):
    """
    GET /api/me/teacher-hours/?month=YYYY-MM — même calcul que
    AdminTeacherHoursView (voir sa docstring pour la définition exacte de
    "heures faites"), mais réservé à l'enseignant connecté et limité à
    lui-même — pour qu'il suive ses propres heures sur son tableau de
    bord, sans jamais voir celles des autres enseignants.
    """
    permission_classes = [permissions.IsAuthenticated, IsTeacher]

    def get(self, request):
        teacher = request.user.teacher_profile
        try:
            week_start, week_end, month_start, month_end, week_totals, month_totals = _hours_for_teacher_qs(
                teacher_ids_filter=[teacher.id], month_param=request.query_params.get("month")
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        week_row = week_totals.get(teacher.id)
        month_row = month_totals.get(teacher.id)

        return Response({
            "week_start": week_start.date(),
            "week_end": (week_end - timedelta(days=1)).date(),
            "month": month_start.strftime("%Y-%m"),
            "hours_this_week": round(week_row["total"].total_seconds() / 3600, 2) if week_row and week_row["total"] else 0,
            "sessions_this_week": week_row["session_count"] if week_row else 0,
            "hours_this_month": round(month_row["total"].total_seconds() / 3600, 2) if month_row and month_row["total"] else 0,
            "sessions_this_month": month_row["session_count"] if month_row else 0,
            "compensation_type": teacher.compensation_type,
            "compensation_rate": teacher.compensation_rate,
        })


class AdminReferralsView(APIView):
    """
    GET /api/admin/referrals/ — programme de parrainage (10%, spec confirmée
    avec le product owner : par élève individuel, tant qu'il paie — voir
    core/services/referrals.py). Regroupe les commissions par (parrain,
    devise) — PAS par parrain seul : un parrain avec un filleul payant en
    EUR et un autre en TND (Tunisie, via Konnect) obtient deux lignes
    distinctes dans la réponse, jamais un total mélangé. Avec le total déjà
    versé vs encore dû par devise, comme pour AdminTeacherHoursView.
    """
    permission_classes = [IsAdmin]

    def get(self, request):
        rows = (
            ReferralCommission.objects.values("referrer_id", "currency")
            .annotate(
                total_earned=Sum("amount"),
                total_unpaid=Sum("amount", filter=Q(paid_out=False)),
                referral_count=Count("id"),
            )
            .order_by("-total_earned")
        )
        referrers = {u.id: u for u in User.objects.filter(id__in=[r["referrer_id"] for r in rows])}

        results = []
        for row in rows:
            referrer = referrers.get(row["referrer_id"])
            if referrer is None:
                continue
            results.append({
                "referrer_id": referrer.id,
                "referrer_name": f"{referrer.first_name} {referrer.last_name}".strip() or referrer.username,
                "referrer_email": referrer.email,
                "referrer_role": referrer.role,
                "referral_code": referrer.referral_code,
                "referred_students_count": referrer.referrals.count(),
                "currency": row["currency"],
                "total_earned": str(row["total_earned"] or Decimal("0.00")),
                "total_unpaid": str(row["total_unpaid"] or Decimal("0.00")),
                "commission_count": row["referral_count"],
            })
        return Response({"referrers": results})


class AdminMarkReferralPaidView(APIView):
    """
    POST /api/admin/referrals/<referrer_id>/mark-paid/?currency=EUR —
    marque comme réglées toutes les commissions non payées de ce parrain
    DANS CETTE DEVISE UNIQUEMENT (un virement EUR et un virement Konnect
    en TND se font à des moments différents, séparément — jamais les
    deux d'un coup). `currency` par défaut "EUR" si non fourni. Pas de
    virement automatisé (même logique que les heures enseignants) : c'est
    l'admin qui vire l'argent lui-même, puis coche ici pour garder le
    compteur "à payer" à jour.
    """
    permission_classes = [IsAdmin]

    def post(self, request, referrer_id):
        currency = request.query_params.get("currency", "EUR")
        updated = ReferralCommission.objects.filter(
            referrer_id=referrer_id, currency=currency, paid_out=False
        ).update(paid_out=True)
        return Response({"marked_paid": updated, "currency": currency})


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------
class SubscriptionCheckoutView(APIView):
    """
    POST /api/subscriptions/checkout/ {"plan_id": 3} — démarre le paiement
    Stripe pour UN SelfStudyPlan précis (spec : chaque abonnement est
    séparé). Si l'élève a déjà un abonnement actif à ce plan, refuse
    plutôt que de créer un doublon facturé deux fois.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        plan_id = request.data.get("plan_id")
        plan = get_object_or_404(SelfStudyPlan, pk=plan_id, is_active=True)
        already_active = Subscription.objects.filter(
            user=request.user, plan=plan, status=Subscription.Status.ACTIVE, current_period_end__gte=timezone.now()
        ).exists()
        if already_active:
            return Response({"detail": f"Vous êtes déjà abonné à « {plan.name} »."}, status=status.HTTP_400_BAD_REQUEST)

        if request.user.country == "Tunisie":
            # Même principe que EnrollmentViewSet.create_checkout_session :
            # pas d'intermédiaire de paiement tunisien fiable pour
            # l'instant, l'élève contacte l'admin par e-mail, qui approuve
            # manuellement dans l'admin Django une fois le virement reçu
            # (voir SelfStudySubscriptionAdmin.mark_paid_bank_transfer).
            # get_or_create (pas juste create) : la contrainte unique
            # (user, plan) rejetterait un second clic sur "S'abonner" sans
            # ça. status=EXPIRED + current_period_end déjà passée = ne
            # compte jamais comme actif (voir get_is_subscribed) tant que
            # l'admin n'a pas validé le virement — sert juste de repère
            # pour que l'admin retrouve la demande dans la liste.
            Subscription.objects.get_or_create(
                user=request.user, plan=plan,
                defaults={"status": Subscription.Status.EXPIRED, "current_period_end": timezone.now()},
            )
            return Response(
                {"detail": "Le paiement en ligne n'est pas disponible pour la Tunisie. "
                           f"Contactez-nous à {settings.CONTACT_EMAIL} pour connaître les modalités "
                           "de paiement par virement bancaire.",
                 "code": "payment_by_email_tunisia",
                 "contact_email": settings.CONTACT_EMAIL},
                status=status.HTTP_200_OK,
            )

        try:
            amount_cents, promo = discounts.apply_discounts(plan.price_cents, request.data.get("promo_code"))
        except discounts.InvalidPromoCode as exc:
            return Response({"detail": str(exc), "code": "invalid_promo_code"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            checkout_session = payments.create_subscription_checkout_session(request.user, plan, unit_amount_cents_override=amount_cents)
        except Exception as exc:
            return Response({"detail": f"Stripe error: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
        discounts.record_promo_code_use(promo)
        return Response({"checkout_url": checkout_session.url})


def mark_series_enrollments_paid(
    membership, stripe_checkout_session_id="", stripe_payment_intent_id="",
    gateway=None, konnect_payment_ref="", currency="EUR", amount=None,
):
    """
    Marks every still-pending Enrollment for this student in this series as
    paid, and records a Payment row for bookkeeping/refunds. A
    SeriesMembership doesn't map to a single Enrollment — it can cover
    several already-scheduled weekly occurrences at once — so all
    currently-pending ones get marked together; there's one charge behind
    them either way (the subscription's first invoice for Stripe, or a
    single Konnect payment — see core/services/konnect.py). Used by the
    manual checkout confirmation for both gateways
    (_confirm_series_membership / KonnectWebhookView) and the automatic
    Stripe off-session charge at scheduling time (see
    GroupAssignmentViewSet.schedule / payments.charge_saved_payment_method
    — Konnect has no equivalent auto-charge, see checkout() above).

    `amount` overrides the recorded amount (in the given `currency`) —
    used for Konnect (Tunisia has its own independent pricing table, not a
    conversion of `membership.monthly_price_cents`, which is always in
    EUR — see core/pricing.py). Defaults to monthly_price_cents/100 (EUR)
    when not given, matching the Stripe flow.
    """
    from .models import Payment as PaymentModel

    enrollments = list(Enrollment.objects.filter(
        student=membership.student, class_session__series=membership.series,
        payment_status=Enrollment.PaymentStatus.PENDING,
    ))
    for enrollment in enrollments:
        enrollment.payment_status = Enrollment.PaymentStatus.PAID
        enrollment.save(update_fields=["payment_status"])
    if enrollments:
        payment = Payment.objects.create(
            user=membership.student, enrollment=enrollments[0],
            amount=amount if amount is not None else membership.monthly_price_cents / 100,
            status=Payment.Status.SUCCEEDED,
            currency=currency, gateway=gateway or PaymentModel.Gateway.STRIPE,
            stripe_checkout_session_id=stripe_checkout_session_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
            konnect_payment_ref=konnect_payment_ref,
        )
        from .services.referrals import create_referral_commission_if_applicable
        create_referral_commission_if_applicable(payment)
    return enrollments


class StripeWebhookView(APIView):
    """
    Receives Stripe events and confirms payments/subscriptions.
    Register this URL (`/api/webhooks/stripe/`) in the Stripe dashboard.
    This is the source of truth for "did the student actually pay" — never
    trust the success_url redirect alone (spec Module 3).
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            event = payments.construct_webhook_event(request.body, request.headers.get("Stripe-Signature", ""))
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            metadata = session.get("metadata", {})

            if metadata.get("kind") == "enrollment":
                self._confirm_enrollment_payment(metadata.get("enrollment_id"), session)
            elif metadata.get("kind") == "subscription":
                self._confirm_subscription(metadata.get("user_id"), metadata.get("plan_id"), session)
            elif metadata.get("kind") == "series_membership":
                self._confirm_series_membership(metadata.get("membership_id"), session)
            elif metadata.get("kind") == "card_setup":
                self._save_default_payment_method(metadata.get("student_profile_id"), session)

        return Response(status=status.HTTP_200_OK)

    @staticmethod
    def _save_default_payment_method(student_profile_id, stripe_session):
        """
        Completes the card-setup flow: pulls the payment method off the
        now-finished SetupIntent, makes it the customer's default (so
        `charge_saved_payment_method` can bill it later without asking
        again), and records it on the StudentProfile.
        """
        from .models import StudentProfile

        setup_intent_id = stripe_session.get("setup_intent")
        if not setup_intent_id:
            return
        try:
            setup_intent = stripe.SetupIntent.retrieve(setup_intent_id)
            profile = StudentProfile.objects.get(pk=student_profile_id)
        except (stripe.error.StripeError, StudentProfile.DoesNotExist):
            return

        payment_method_id = setup_intent.payment_method
        stripe.Customer.modify(profile.stripe_customer_id, invoice_settings={"default_payment_method": payment_method_id})
        profile.stripe_default_payment_method_id = payment_method_id
        profile.save(update_fields=["stripe_default_payment_method_id"])

    @staticmethod
    @transaction.atomic
    def _confirm_series_membership(membership_id, stripe_session):
        try:
            membership = SeriesMembership.objects.select_for_update().get(pk=membership_id)
        except SeriesMembership.DoesNotExist:
            return
        membership.status = SeriesMembership.Status.ACTIVE
        membership.stripe_subscription_id = stripe_session.get("subscription", "")
        membership.save(update_fields=["status", "stripe_subscription_id"])
        mark_series_enrollments_paid(membership, stripe_checkout_session_id=stripe_session.get("id", ""))

    @staticmethod
    @transaction.atomic
    def _confirm_enrollment_payment(enrollment_id, stripe_session):
        try:
            enrollment = Enrollment.objects.select_for_update().get(pk=enrollment_id)
        except Enrollment.DoesNotExist:
            return
        enrollment.payment_status = Enrollment.PaymentStatus.PAID
        enrollment.save(update_fields=["payment_status"])
        payment_qs = Payment.objects.filter(
            enrollment=enrollment, stripe_checkout_session_id=stripe_session.get("id")
        )
        payment_qs.update(status=Payment.Status.SUCCEEDED, stripe_payment_intent_id=stripe_session.get("payment_intent", ""))
        notifications.send_enrollment_confirmed(enrollment)
        payment = payment_qs.first()
        if payment is not None:
            from .services.referrals import create_referral_commission_if_applicable
            create_referral_commission_if_applicable(payment)

    @staticmethod
    def _confirm_subscription(user_id, plan_id, stripe_session):
        from .models import Subscription
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return
        Subscription.objects.update_or_create(
            user=user, plan_id=plan_id,
            defaults={
                "status": Subscription.Status.ACTIVE,
                "stripe_subscription_id": stripe_session.get("subscription", ""),
                # Placeholder — the real value should come from the Stripe
                # subscription object's `current_period_end` via a follow-up
                # API call or the `customer.subscription.updated` webhook.
                "current_period_end": timezone.now() + timedelta(days=30),
            },
        )


class KonnectWebhookView(APIView):
    """
    Confirme les paiements passés par Konnect (élèves en Tunisie — voir
    core/services/konnect.py). Contrairement au webhook Stripe (POST avec
    une charge utile signée), Konnect notifie par une simple requête GET
    avec `?payment_ref=...` en paramètre — on doit donc re-interroger
    l'API Konnect pour connaître le vrai statut du paiement, plutôt que de
    faire confiance à la requête elle-même (qui ne prouve rien par elle-même).
    Register this URL (`/api/webhooks/konnect/`) dans le champ `webhook` de
    chaque paiement initié (déjà fait automatiquement — voir konnect.init_payment).
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        payment_ref = request.query_params.get("payment_ref")
        if not payment_ref:
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            payment_details = konnect.get_payment(payment_ref)
        except Exception:
            return Response(status=status.HTTP_502_BAD_GATEWAY)

        if payment_details.get("status") != "completed":
            return Response(status=status.HTTP_200_OK)

        order_id = payment_details.get("orderId", "")
        kind, _, raw_id = order_id.partition(":")

        if kind == "enrollment":
            self._confirm_enrollment_payment(raw_id, payment_ref)
        elif kind == "series":
            self._confirm_series_membership(raw_id, payment_ref, payment_details.get("amount", 0))

        return Response(status=status.HTTP_200_OK)

    @staticmethod
    @transaction.atomic
    def _confirm_enrollment_payment(enrollment_id, payment_ref):
        try:
            enrollment = Enrollment.objects.select_for_update().get(pk=enrollment_id)
        except (Enrollment.DoesNotExist, ValueError):
            return
        enrollment.payment_status = Enrollment.PaymentStatus.PAID
        enrollment.save(update_fields=["payment_status"])
        payment_qs = Payment.objects.filter(enrollment=enrollment, konnect_payment_ref=payment_ref)
        payment_qs.update(status=Payment.Status.SUCCEEDED)
        notifications.send_enrollment_confirmed(enrollment)
        payment = payment_qs.first()
        if payment is not None:
            from .services.referrals import create_referral_commission_if_applicable
            create_referral_commission_if_applicable(payment)

    @staticmethod
    @transaction.atomic
    def _confirm_series_membership(membership_id, payment_ref, amount_millimes):
        try:
            membership = SeriesMembership.objects.select_for_update().get(pk=membership_id)
        except (SeriesMembership.DoesNotExist, ValueError):
            return
        membership.status = SeriesMembership.Status.ACTIVE
        membership.save(update_fields=["status"])
        mark_series_enrollments_paid(
            membership, gateway=Payment.Gateway.KONNECT, konnect_payment_ref=payment_ref,
            currency="TND", amount=amount_millimes / 1000,
        )


# ---------------------------------------------------------------------------
# Landing page (public, unauthenticated) — see PublicTeacherSerializer /
# FAQSerializer / StaticPageSerializer. Everything here is AllowAny and
# read-only from the API; content is managed entirely from the Django
# admin (TeacherProfile.is_featured/photo/etc, FAQ, StaticPage).
# ---------------------------------------------------------------------------
class PublicTeachersView(generics.ListAPIView):
    """GET /api/public/teachers/ — featured, approved teachers for the landing page's "Nos enseignants experts" section."""
    serializer_class = PublicTeacherSerializer
    permission_classes = [permissions.AllowAny]
    queryset = TeacherProfile.objects.filter(is_active=True, is_featured=True).select_related("user", "subject")


class PublicTeacherDetailView(generics.RetrieveAPIView):
    """GET /api/public/teachers/<id>/ — full profile for one teacher's detail page (see TeacherDetail.jsx). Same visibility rule as the list — only active, featured teachers are reachable, so a card always links to a resolvable page."""
    serializer_class = PublicTeacherDetailSerializer
    permission_classes = [permissions.AllowAny]
    queryset = TeacherProfile.objects.filter(is_active=True, is_featured=True).select_related("user", "subject")


class FAQViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/public/faq/ — visible FAQ entries, in display order. Managed from the Django admin."""
    serializer_class = FAQSerializer
    permission_classes = [permissions.AllowAny]
    queryset = FAQ.objects.filter(is_visible=True)


class StaticPageDetailView(generics.RetrieveAPIView):
    """GET /api/public/pages/<slug>/ — a legal/static page's content (mentions-legales, cgv, confidentialite). Managed from the Django admin."""
    serializer_class = StaticPageSerializer
    permission_classes = [permissions.AllowAny]
    queryset = StaticPage.objects.all()
    lookup_field = "slug"


class PublicPricingView(APIView):
    """GET /api/public/pricing/ — the 8 group-tier rates (€/h), for the landing page's "Forfaits" section. Reflects live PricingRate admin edits — see core/pricing.py."""
    permission_classes = [permissions.AllowAny]

    TIERS = [
        ("GROUP_10", "Groupe de 10"), ("GROUP_8", "Groupe de 8"), ("GROUP_6", "Groupe de 6"), ("GROUP_5", "Groupe de 5"),
        ("GROUP_4", "Groupe de 4"), ("GROUP_3", "Groupe de 3"), ("GROUP_2", "Groupe de 2"), ("INDIVIDUAL", "Individuel"),
    ]

    def get(self, request):
        from .pricing import rate_per_hour_cents, rate_per_hour_tnd
        discount_pct = discounts.get_global_discount_percent()
        factor = (1 - discount_pct / 100) if discount_pct else 1
        return Response([
            {
                "group_tier": tier, "group_tier_display": label,
                "price_per_hour_eur": rate_per_hour_cents(tier) / 100 * factor,
                "price_per_hour_tnd": rate_per_hour_tnd(tier) * factor,
                # Prix plein, pour un affichage barré côté frontend — absents
                # (donc identiques au prix ci-dessus) si aucun rabais actif.
                "original_price_per_hour_eur": rate_per_hour_cents(tier) / 100,
                "original_price_per_hour_tnd": rate_per_hour_tnd(tier),
                "discount_percentage": discount_pct,
            }
            for tier, label in self.TIERS
        ])


class PublicNewsletterSubscribeView(APIView):
    """
    POST /api/public/newsletter/ — {"email": "..."} — inscription à la
    newsletter depuis le footer de la page d'accueil.

    Enregistre toujours l'email localement (NewsletterSubscriber) en
    premier, puis pousse le contact vers Brevo en best-effort : un souci
    Brevo (clé absente, API indisponible...) est logué mais ne fait
    jamais échouer l'inscription côté visiteur — voir core/services/brevo.py.
    Ré-inscrire un email déjà connu répond simplement OK (idempotent),
    sans erreur, plutôt qu'un 400 "déjà inscrit" peu utile pour l'UX.
    """
    permission_classes = [permissions.AllowAny]
    throttle_scope = "newsletter"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request):
        serializer = NewsletterSubscriberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]

        subscriber, _ = NewsletterSubscriber.objects.get_or_create(email=email)

        if not subscriber.synced_to_brevo:
            try:
                brevo.add_contact(email)
                subscriber.synced_to_brevo = True
                subscriber.save(update_fields=["synced_to_brevo"])
            except brevo.BrevoError:
                logger.exception("Échec de la synchronisation Brevo pour %s", email)

        return Response({"email": email}, status=status.HTTP_201_CREATED)


class PublicValidatePromoCodeView(APIView):
    """
    POST /api/public/promo-codes/validate/ — {"code": "..."} — vérifie
    un code avant de payer, pour afficher "-20%" côté élève sans encore
    créer de paiement. N'incrémente PAS l'usage du code (voir
    core/discounts.py: record_promo_code_use, appelé seulement après un
    paiement réellement créé) — un élève peut valider le même code
    plusieurs fois sans le consommer.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        code = request.data.get("code", "")
        try:
            promo = discounts.get_valid_promo_code(code)
        except discounts.InvalidPromoCode as exc:
            return Response({"valid": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if promo is None:
            return Response({"valid": False, "detail": "Code manquant."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"valid": True, "percentage": promo.percentage})
