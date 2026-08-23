from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import (
    BacType, CecrlLevel, ClassSession, Enrollment, FAQ, ForumReply, ForumThread,
    GroupAnnouncement, GroupAssignment, GroupRequest, Material, NewsletterSubscriber, ParentalConsent,
    SeriesMembership, StaticPage, StudentProfile, Subject, TeacherProfile,
    SelfStudyContentItem, SelfStudyPlan, Subscription, TeacherSubject, VideoProgress,
)
from .services import notifications

User = get_user_model()


# ---------------------------------------------------------------------------
# Subjects & teachers
# ---------------------------------------------------------------------------
class SubjectSerializer(serializers.ModelSerializer):
    reference_monthly_prices_1ere = serializers.SerializerMethodField()
    reference_monthly_prices_terminale = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = [
            "id", "name", "code", "level", "bac_type", "subject_type",
            "hours_per_week_premiere", "hours_per_week_terminale",
            "reference_monthly_prices_1ere", "reference_monthly_prices_terminale",
        ]

    def get_reference_monthly_prices_1ere(self, obj):
        return {k: v / 100 for k, v in reference_monthly_prices_cents(obj, "1ere").items()}

    def get_reference_monthly_prices_terminale(self, obj):
        return {k: v / 100 for k, v in reference_monthly_prices_cents(obj, "terminale").items()}


# ---------------------------------------------------------------------------
# Users / registration
# ---------------------------------------------------------------------------
class StudentProfileSerializer(serializers.ModelSerializer):
    premiere_specialties = SubjectSerializer(many=True, read_only=True)
    terminale_specialties = SubjectSerializer(many=True, read_only=True)
    terminale_math_option = SubjectSerializer(read_only=True)
    has_payment_method = serializers.BooleanField(read_only=True)
    is_minor = serializers.BooleanField(read_only=True)
    requires_parental_consent = serializers.BooleanField(read_only=True)
    parental_consent_status = serializers.SerializerMethodField()
    parent_email = serializers.SerializerMethodField()

    class Meta:
        model = StudentProfile
        fields = [
            "bac_type", "grade_level", "cecrl_level", "candidate_type", "timezone", "has_payment_method",
            "premiere_specialties", "terminale_specialties", "terminale_math_option",
            "is_minor", "requires_parental_consent", "parental_consent_status", "parent_email",
        ]

    def get_parental_consent_status(self, obj):
        consent = getattr(obj, "parental_consent", None)
        return consent.status if consent else None

    def get_parent_email(self, obj):
        consent = getattr(obj, "parental_consent", None)
        return consent.parent_email if consent else None


def _validate_math_option_consistency(terminale_specialties, math_option):
    """
    Maths Expertes suppose d'avoir gardé Mathématiques en spécialité de
    Terminale ; Maths Complémentaires, à l'inverse, suppose de l'avoir
    abandonnée. Partagé entre StudentSpecialtiesUpdateSerializer et
    StudentRegistrationSerializer pour ne pas dupliquer la règle.
    """
    if not math_option:
        return
    has_maths_specialty = any(s.code == "gen-maths" for s in terminale_specialties)
    if math_option.code == "gen-maths-expertes" and not has_maths_specialty:
        raise serializers.ValidationError({
            "terminale_math_option": "Mathématiques Expertes suppose d'avoir gardé Mathématiques "
                                      "en spécialité de Terminale."
        })
    if math_option.code == "gen-maths-complementaires" and has_maths_specialty:
        raise serializers.ValidationError({
            "terminale_math_option": "Mathématiques Complémentaires est réservée aux élèves qui ont "
                                      "abandonné Mathématiques en spécialité de Terminale."
        })


class StudentSpecialtiesUpdateSerializer(serializers.Serializer):
    """
    Lets a student change their specialty choices after registration (e.g.
    they realize they picked the wrong ones, or move from Première to
    Terminale and need to drop one). Same 3/2 cap as at registration.
    """
    premiere_specialties = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(subject_type=Subject.SubjectType.SPECIALTY), many=True, required=False,
    )
    terminale_specialties = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(subject_type=Subject.SubjectType.SPECIALTY), many=True, required=False,
    )
    # Maths Expertes / Maths Complémentaires — ne compte PAS dans le
    # plafond de 2 spécialités Terminale ci-dessus, c'est un choix séparé
    # (voir Subject.SubjectType.MATH_OPTION / StudentProfile.terminale_math_option).
    terminale_math_option = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(subject_type=Subject.SubjectType.MATH_OPTION),
        required=False, allow_null=True,
    )
    # Niveau CECRL — pour un élève FLE/FLS qui progresse (ex: passe de A1
    # à A2). Sans effet pour un élève Général/Techno/Pro, laissé vide.
    cecrl_level = serializers.ChoiceField(choices=CecrlLevel.choices, required=False, allow_blank=True)

    def validate_premiere_specialties(self, value):
        if len(value) > 3:
            raise serializers.ValidationError("A student may pick at most 3 specialties for Première.")
        return value

    def validate_terminale_specialties(self, value):
        if len(value) > 2:
            raise serializers.ValidationError("A student may keep at most 2 specialties for Terminale.")
        return value

    def validate(self, attrs):
        instance = self.instance
        terminale_specialties = attrs.get(
            "terminale_specialties",
            list(instance.terminale_specialties.all()) if instance else [],
        )
        math_option = attrs.get(
            "terminale_math_option", instance.terminale_math_option if instance else None
        )
        _validate_math_option_consistency(terminale_specialties, math_option)
        return attrs

    def update(self, instance, validated_data):
        if "premiere_specialties" in validated_data:
            instance.premiere_specialties.set(validated_data["premiere_specialties"])
        if "terminale_specialties" in validated_data:
            instance.terminale_specialties.set(validated_data["terminale_specialties"])
        if "terminale_math_option" in validated_data:
            instance.terminale_math_option = validated_data["terminale_math_option"]
            instance.save(update_fields=["terminale_math_option"])
        if "cecrl_level" in validated_data:
            instance.cecrl_level = validated_data["cecrl_level"]
            instance.save(update_fields=["cecrl_level"])
        return instance


class TeacherProfileBriefSerializer(serializers.ModelSerializer):
    """Minimal teacher-profile info embedded in UserSerializer — just enough for the dashboard to know if the account is still awaiting admin approval."""
    class Meta:
        model = TeacherProfile
        fields = ["id", "is_active", "bio", "default_meeting_url"]


class UserSerializer(serializers.ModelSerializer):
    student_profile = StudentProfileSerializer(read_only=True)
    teacher_profile = serializers.SerializerMethodField()
    referral_earnings_total = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "username", "first_name", "last_name", "phone", "country", "role",
            "student_profile", "teacher_profile", "referral_code", "referral_earnings_total",
        ]
        read_only_fields = ["id", "role", "referral_code"]

    def get_teacher_profile(self, obj):
        if obj.role != User.Role.TEACHER or not hasattr(obj, "teacher_profile"):
            return None
        return TeacherProfileBriefSerializer(obj.teacher_profile).data

    def get_referral_earnings_total(self, obj):
        # Groupé par devise — un total EUR et un total TND ne veulent
        # rien dire additionnés (voir core/services/referrals.py).
        rows = obj.referral_commissions_earned.values("currency").annotate(total=Sum("amount"))
        return {row["currency"]: str(row["total"]) for row in rows}


def _resolve_referrer(referral_code):
    """
    Résout un code de parrainage optionnel (?ref=CODE dans le lien
    d'inscription) vers le User correspondant. Un code absent, vide, ou
    qui ne correspond à personne renvoie simplement None — un code
    invalide ne doit jamais bloquer une inscription. Partagé entre les 3
    serializers d'inscription (élève/enseignant/affilié).
    """
    if not referral_code:
        return None
    return User.objects.filter(referral_code__iexact=referral_code).first()


class StudentRegistrationSerializer(serializers.ModelSerializer):
    """
    Public self-registration endpoint for students/parents. Teacher and admin
    accounts are NOT created through this serializer — they are onboarded by
    an admin (spec 3.C: "Onboard, review, and approve teacher accounts").
    """
    password = serializers.CharField(write_only=True, validators=[validate_password])
    # Overrides the auto-generated UniqueValidator DRF adds for `email`
    # now that it's unique=True at the model level — our own
    # case-insensitive check below (validate_email) is stricter and gives
    # a friendlier French message; the DB constraint remains as the real
    # safety net against race conditions either way.
    email = serializers.EmailField(validators=[])
    bac_type = serializers.ChoiceField(choices=BacType.choices, write_only=True)
    # required=False : obligatoire seulement pour Général/Techno/Pro, pas
    # pour FLE/FLS (voir validate() ci-dessous) — Première/Terminale n'a
    # pas de sens pour ces deux parcours.
    grade_level = serializers.ChoiceField(choices=StudentProfile.GradeLevel.choices, write_only=True, required=False)
    # L'équivalent de grade_level pour FLE/FLS — obligatoire uniquement
    # pour ces deux parcours, voir validate().
    cecrl_level = serializers.ChoiceField(choices=CecrlLevel.choices, write_only=True, required=False, allow_blank=True)
    # No longer collected at registration (KLASSX serves all students, not
    # just candidats libres) — every self-registered account defaults to
    # "standard"; an admin can still change it per-student from the Django
    # admin if a genuine candidat libre case needs it.
    candidate_type = serializers.HiddenField(default=StudentProfile.CandidateType.STANDARD)
    # Bac reform: up to 3 specialties chosen for Première, of which 2 are
    # kept for Terminale (the two lists can differ — e.g. a student who
    # dropped Maths after Première won't have it in terminale_specialties).
    # Only meaningful for bac_type=GENERAL — see validate().
    premiere_specialties = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(subject_type=Subject.SubjectType.SPECIALTY),
        many=True, write_only=True, required=False, default=list,
    )
    terminale_specialties = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(subject_type=Subject.SubjectType.SPECIALTY),
        many=True, write_only=True, required=False, default=list,
    )
    # Maths Expertes / Maths Complémentaires — pas une 3e spécialité, ne
    # compte pas dans le plafond ci-dessus (voir Subject.SubjectType.
    # MATH_OPTION / StudentProfile.terminale_math_option).
    terminale_math_option = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.filter(subject_type=Subject.SubjectType.MATH_OPTION),
        write_only=True, required=False, allow_null=True, default=None,
    )
    # Programme de parrainage (spec: ouvert à tout type de compte, pas
    # seulement aux enseignants) — le code présent dans le lien utilisé
    # pour arriver sur la page d'inscription (?ref=CODE), s'il y en a un.
    # Un code inconnu/invalide ne bloque jamais l'inscription, il est
    # simplement ignoré — voir create().
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    # RGPD / consentement parental — voir StudentProfile.date_of_birth et
    # ParentalConsent. date_of_birth est obligatoire pour toute nouvelle
    # inscription. Si l'élève est mineur, `email` est l'email DU PARENT
    # (utilisé comme identifiant de connexion du compte, unique et
    # partagé — voir ParentalConsent) et parent_full_name devient
    # obligatoire (validate() ci-dessous) ; c'est l'inscription faite
    # ensemble, avec un mot de passe choisi en commun, qui vaut
    # consentement — pas d'étape ni d'email de confirmation séparés.
    date_of_birth = serializers.DateField(write_only=True)
    parent_full_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = [
            "email", "username", "password", "first_name", "last_name", "phone", "country",
            "bac_type", "grade_level", "cecrl_level", "candidate_type", "premiere_specialties", "terminale_specialties",
            "terminale_math_option", "referral_code", "date_of_birth", "parent_full_name",
        ]

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Un compte existe déjà avec cet email.")
        return value

    def validate_date_of_birth(self, value):
        if value > date.today():
            raise serializers.ValidationError("La date de naissance ne peut pas être dans le futur.")
        return value

    def validate_premiere_specialties(self, value):
        if len(value) > 3:
            raise serializers.ValidationError("A student may pick at most 3 specialties for Première.")
        return value

    def validate_terminale_specialties(self, value):
        if len(value) > 2:
            raise serializers.ValidationError("A student may keep at most 2 specialties for Terminale.")
        return value

    def validate(self, attrs):
        dob = attrs.get("date_of_birth")
        today = date.today()
        is_minor = dob and (today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))) < 18
        if is_minor and not attrs.get("parent_full_name", "").strip():
            raise serializers.ValidationError(
                {"parent_full_name": "Obligatoire pour un élève mineur : nom du parent ou tuteur légal. "
                                      "Utilisez l'email du parent comme email de connexion ci-dessus."}
            )
        bac_type = attrs.get("bac_type")
        if bac_type in (BacType.FLE, BacType.FLS):
            if bac_type == BacType.FLE and not attrs.get("cecrl_level"):
                raise serializers.ValidationError(
                    {"cecrl_level": "Obligatoire pour le parcours FLE (niveau A1 à C2)."}
                )
        elif not attrs.get("grade_level"):
            raise serializers.ValidationError({"grade_level": "Obligatoire pour ce parcours."})
        if bac_type == BacType.GENERAL:
            _validate_math_option_consistency(
                attrs.get("terminale_specialties") or [], attrs.get("terminale_math_option")
            )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        bac_type = validated_data.pop("bac_type")
        grade_level = validated_data.pop("grade_level", "")
        cecrl_level = validated_data.pop("cecrl_level", "")
        candidate_type = validated_data.pop("candidate_type")
        premiere_specialties = validated_data.pop("premiere_specialties", [])
        terminale_specialties = validated_data.pop("terminale_specialties", [])
        terminale_math_option = validated_data.pop("terminale_math_option", None)
        password = validated_data.pop("password")
        referral_code = validated_data.pop("referral_code", "")
        date_of_birth = validated_data.pop("date_of_birth")
        parent_full_name = validated_data.pop("parent_full_name", "").strip()

        user = User(role=User.Role.STUDENT, **validated_data)
        user.set_password(password)
        user.referred_by = _resolve_referrer(referral_code)
        try:
            user.save()
        except IntegrityError:
            # The pre-check in validate_email passed, but another request
            # for the same email won the race and saved first — extremely
            # rare, but the unique constraint on User.email makes it
            # possible. Surface it the same way validate_email would.
            raise serializers.ValidationError({"email": "Un compte existe déjà avec cet email."})

        profile = StudentProfile.objects.create(
            user=user, bac_type=bac_type, grade_level=grade_level, cecrl_level=cecrl_level,
            candidate_type=candidate_type, date_of_birth=date_of_birth,
        )
        # Specialties only make sense for Bac Général (Technologique/
        # Professionnel students follow their série/filière's fixed
        # curriculum instead of picking specialties) — silently ignore any
        # sent for another track rather than erroring, since the frontend
        # simply won't show that picker for them.
        if bac_type == BacType.GENERAL:
            if premiere_specialties:
                profile.premiere_specialties.set(premiere_specialties)
            if terminale_specialties:
                profile.terminale_specialties.set(terminale_specialties)
            if terminale_math_option:
                profile.terminale_math_option = terminale_math_option
                profile.save(update_fields=["terminale_math_option"])

        if profile.is_minor:
            # L'inscription faite ensemble (email+mot de passe du parent,
            # nom du parent ET de l'élève) vaut consentement immédiat —
            # voir ParentalConsent. Pas d'email à cliquer, pas d'attente.
            request = self.context.get("request")
            remote_addr = request.META.get("REMOTE_ADDR") if request else None
            ParentalConsent.objects.create(
                student=profile, parent_full_name=parent_full_name, parent_email=user.email,
                status=ParentalConsent.Status.CONFIRMED, confirmed_at=timezone.now(), confirmed_ip=remote_addr,
            )

        notifications.send_registration_confirmation(user)
        return user


class TeacherRegistrationSerializer(serializers.ModelSerializer):
    """
    Public self-registration for teachers. Creates the User (role=TEACHER)
    and a TeacherProfile, but `TeacherProfile.is_active` stays False —
    same as before, an admin still has to review and approve the account
    (spec 3.C) before it can be assigned any group or session; see
    TeacherProfileViewSet.approve. The account can log in right away, but
    the teacher dashboard should show a "pending approval" notice until
    then (see UserSerializer.teacher_profile.is_active).
    """
    password = serializers.CharField(write_only=True, validators=[validate_password])
    # See StudentRegistrationSerializer for why this overrides DRF's
    # auto-generated UniqueValidator on `email`.
    email = serializers.EmailField(validators=[])
    bio = serializers.CharField(write_only=True, required=False, allow_blank=True, default="")
    subjects = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.all(), many=True, write_only=True, required=False, default=list,
        help_text="Subjects this teacher can teach — sets up their TeacherSubject rows.",
    )
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ["email", "username", "password", "first_name", "last_name", "phone", "country", "bio", "subjects", "referral_code"]

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Un compte existe déjà avec cet email.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        bio = validated_data.pop("bio", "")
        subjects = validated_data.pop("subjects", [])
        password = validated_data.pop("password")
        referral_code = validated_data.pop("referral_code", "")

        user = User(role=User.Role.TEACHER, **validated_data)
        user.set_password(password)
        user.referred_by = _resolve_referrer(referral_code)
        try:
            user.save()
        except IntegrityError:
            raise serializers.ValidationError({"email": "Un compte existe déjà avec cet email."})

        teacher_profile = TeacherProfile.objects.create(user=user, bio=bio)
        for subject in subjects:
            TeacherSubject.objects.get_or_create(teacher=teacher_profile, subject=subject)
        notifications.send_registration_confirmation(user)
        return user


class AffiliateRegistrationSerializer(serializers.ModelSerializer):
    """
    Inscription "pure affilié" (spec: programme de parrainage ouvert à
    tout le monde, pas seulement aux élèves/enseignants — quelqu'un qui
    n'a par ailleurs aucun autre rôle sur KLASSX, juste un lien à
    partager). Volontairement minimale : pas de bac/matières/bio, juste
    de quoi créer le compte et récupérer un `referral_code`.
    """
    password = serializers.CharField(write_only=True, validators=[validate_password])
    email = serializers.EmailField(validators=[])
    referral_code = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ["email", "username", "password", "first_name", "last_name", "phone", "country", "referral_code"]

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Un compte existe déjà avec cet email.")
        return value

    @transaction.atomic
    def create(self, validated_data):
        password = validated_data.pop("password")
        referral_code = validated_data.pop("referral_code", "")

        user = User(role=User.Role.AFFILIATE, **validated_data)
        user.set_password(password)
        user.referred_by = _resolve_referrer(referral_code)
        try:
            user.save()
        except IntegrityError:
            raise serializers.ValidationError({"email": "Un compte existe déjà avec cet email."})

        notifications.send_registration_confirmation(user)
        return user


# ---------------------------------------------------------------------------
# Subjects & teachers
# ---------------------------------------------------------------------------
class TeacherProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)
    subjects = serializers.SerializerMethodField()

    class Meta:
        model = TeacherProfile
        fields = ["id", "full_name", "email", "bio", "is_active", "subjects"]
        # Deliberately excludes default_meeting_url/google_account_email/
        # google_connected — those are the teacher's own settings, only
        # exposed via TeacherSettingsSerializer (see /api/teachers/me/).

    def get_subjects(self, obj):
        return [ts.subject.name for ts in obj.subjects.select_related("subject").all()]


class TeacherSettingsSerializer(serializers.ModelSerializer):
    """
    The logged-in teacher's own settings — self-service video-link setup
    for the autonomous scheduling model (see TeacherProfile). Used by
    `GET/PATCH /api/teachers/me/`.
    """
    google_connected = serializers.BooleanField(read_only=True)

    class Meta:
        model = TeacherProfile
        fields = ["id", "default_meeting_url", "google_account_email", "google_connected"]
        read_only_fields = ["id", "google_account_email"]
        # google_oauth_refresh_token is never included — it's set only by
        # TeacherGoogleCallbackView, never read or written through the API.


# ---------------------------------------------------------------------------
# Video capsules
# ---------------------------------------------------------------------------
class SelfStudyPlanSerializer(serializers.ModelSerializer):
    price_eur = serializers.SerializerMethodField()
    price_tnd = serializers.SerializerMethodField()
    is_subscribed = serializers.SerializerMethodField()

    class Meta:
        model = SelfStudyPlan
        fields = ["id", "code", "name", "price_cents", "price_eur", "price_millimes_tnd", "price_tnd", "is_active", "is_subscribed"]

    def get_price_eur(self, obj):
        return obj.price_cents / 100

    def get_price_tnd(self, obj):
        return obj.price_millimes_tnd / 1000

    def get_is_subscribed(self, obj):
        """True si l'utilisateur connecté a un abonnement ACTIF à CE plan précisément — jamais aux autres."""
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        return obj.subscriptions.filter(
            user=request.user, status=Subscription.Status.ACTIVE, current_period_end__gte=timezone.now()
        ).exists()


class SelfStudyContentItemSerializer(serializers.ModelSerializer):
    plan_name = serializers.CharField(source="plan.name", read_only=True)

    class Meta:
        model = SelfStudyContentItem
        fields = [
            "id", "plan", "plan_name", "content_type", "title", "description", "chapter_name",
            "month", "order_index", "duration_seconds",
        ]
        # video_provider_id/pdf_file volontairement exclus : l'URL réelle
        # (visionnage ou téléchargement) n'est jamais renvoyée ici, voir
        # les actions dédiées playback_url/download_url qui vérifient
        # l'abonnement d'abord. is_unlocked exclu aussi — un item non
        # débloqué n'apparaît simplement jamais dans la liste (voir
        # SelfStudyContentViewSet.get_queryset), donc ce champ n'a pas
        # besoin d'être exposé côté élève.


class VideoProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoProgress
        fields = ["id", "capsule", "progress_percentage", "updated_at"]
        read_only_fields = ["id", "updated_at"]


# ---------------------------------------------------------------------------
# Class sessions & enrollments
# ---------------------------------------------------------------------------
from .pricing import (
    WEEKLY_HOURS_PACKAGES, rate_per_hour_cents,
    reference_monthly_prices_cents, session_price_cents,
)


class ClassSessionSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    seats_taken = serializers.IntegerField(source="confirmed_seats_taken", read_only=True)
    seats_available = serializers.SerializerMethodField()
    price_per_hour_eur = serializers.SerializerMethodField()
    price_eur = serializers.SerializerMethodField()

    class Meta:
        model = ClassSession
        fields = [
            "id", "subject", "subject_name", "level", "group_tier", "max_capacity",
            "min_students", "min_enrollment_deadline", "assigned_teacher", "series",
            "start_time", "end_time", "status", "meeting_url", "recording_url",
            "seats_taken", "seats_available", "price_per_hour_eur", "price_eur",
        ]
        read_only_fields = ["assigned_teacher", "series", "status", "meeting_url", "recording_url"]

    def get_seats_available(self, obj):
        return max(obj.max_capacity - obj.confirmed_seats_taken, 0)

    def get_price_per_hour_eur(self, obj):
        return rate_per_hour_cents(obj.group_tier) / 100

    def get_price_eur(self, obj):
        return session_price_cents(obj) / 100

    def validate(self, attrs):
        group_tier = attrs.get("group_tier", getattr(self.instance, "group_tier", None))
        max_capacity = attrs.get("max_capacity")
        expected = ClassSession.TIER_CAPACITY.get(group_tier)
        if max_capacity and expected and max_capacity != expected:
            raise serializers.ValidationError(
                f"max_capacity for {group_tier} must be {expected}."
            )
        return attrs


class EnrollmentSerializer(serializers.ModelSerializer):
    """
    Creating an enrollment is a booking request. Seat vs. waitlist assignment
    and payment_status transitions are handled in the view/service layer,
    not here — see core.views.EnrollmentViewSet and spec 5.1 / Module 3.
    `class_session` stays a writable PK (used when creating), while
    `class_session_detail` gives the frontend the nested session info
    (subject, time, price, meeting link) it needs to render a dashboard
    entry without a second request per enrollment.
    """
    class_session_detail = ClassSessionSerializer(source="class_session", read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id", "student", "class_session", "class_session_detail", "payment_status",
            "waitlisted", "booked_at", "cancelled_at", "cancellation_reason",
        ]
        read_only_fields = ["student", "payment_status", "waitlisted", "booked_at", "cancelled_at"]


# ---------------------------------------------------------------------------
# Group requests (new booking flow — student expresses interest, admin
# assembles the group and schedules it; see GroupRequest model docstring).
# ---------------------------------------------------------------------------
class SeriesMembershipSerializer(serializers.ModelSerializer):
    subject_name = serializers.CharField(source="series.subject.name", read_only=True)
    group_tier = serializers.CharField(source="series.group_tier", read_only=True)
    weekly_hours = serializers.IntegerField(source="series.weekly_hours", read_only=True)
    weekly_hours_display = serializers.SerializerMethodField()
    weekday = serializers.IntegerField(source="series.weekday", read_only=True)
    start_time = serializers.TimeField(source="series.start_time", read_only=True)
    group_assignment = serializers.IntegerField(source="series.group_assignment_id", read_only=True)
    monthly_price_eur = serializers.SerializerMethodField()

    class Meta:
        model = SeriesMembership
        fields = [
            "id", "student", "series", "subject_name", "group_tier", "weekly_hours", "weekly_hours_display",
            "weekday", "start_time", "group_assignment", "status", "is_billable", "monthly_price_cents",
            "monthly_price_eur", "joined_at", "leave_requested_at", "leaves_on",
        ]

    def get_weekly_hours_display(self, obj):
        from .pricing import WEEKLY_HOURS_LABELS
        return WEEKLY_HOURS_LABELS.get(obj.series.weekly_hours) if obj.series.weekly_hours else None
        read_only_fields = [
            "student", "status", "monthly_price_cents", "joined_at", "leave_requested_at", "leaves_on",
        ]

    def get_monthly_price_eur(self, obj):
        return obj.monthly_price_cents / 100 if obj.is_billable else 0


def validate_specialty_access(subject, level, user):
    """
    Shared rules used by both GroupRequestSerializer and
    IndividualBookingSerializer:
    0. A subject tagged for a single level (e.g. Français is Première-only,
       Philosophie is Terminale-only) can't be requested at the other
       level — applies regardless of role.
    1. A subject must match the student's Bac track (Général/Technologique/
       Professionnel) — the catalogs differ per track (see Subject.bac_type).
    2. Within that, a SPECIALTY subject (spec: "chaque groupe doit être avec
       les mêmes spécialités sauf pour les matières de tronc commun") can
       only be requested/booked if it's one of the student's chosen
       specialties for that level — only meaningful for Bac Général.
       COMMON_CORE subjects have no such restriction.
    3. A MATH_OPTION subject (Maths Expertes / Maths Complémentaires — see
       Subject.SubjectType.MATH_OPTION) can only be requested/booked if
       it's the student's chosen terminale_math_option — NOT checked
       against terminale_specialties, since it isn't one (a student's 2
       kept specialties and their math option are two separate choices).
    Raises serializers.ValidationError if not allowed.
    """
    if subject and subject.level != Subject.Level.BOTH and subject.level != level:
        raise serializers.ValidationError(
            f"{subject.name} n'est proposée qu'en {subject.get_level_display()}."
        )

    if not (user and getattr(user, "role", None) == "student"):
        return
    profile = getattr(user, "student_profile", None)

    if subject and profile and subject.bac_type != profile.bac_type:
        raise serializers.ValidationError(
            f"{subject.name} n'appartient pas à la filière ({profile.get_bac_type_display()}) de votre profil."
        )

    if subject and subject.subject_type == Subject.SubjectType.MATH_OPTION:
        if profile and profile.bac_type != BacType.GENERAL:
            return
        if not (profile and profile.terminale_math_option_id == subject.id):
            raise serializers.ValidationError(
                f"{subject.name} n'est pas l'option mathématiques choisie sur votre profil. "
                f"Mettez à jour votre profil d'abord."
            )
        return

    if not (subject and subject.subject_type == Subject.SubjectType.SPECIALTY):
        return
    if profile and profile.bac_type != BacType.GENERAL:
        # Technologique/Professionnel don't gate on chosen specialties —
        # their whole catalog is effectively fixed per série/filière.
        return
    chosen = (
        profile.premiere_specialties.all() if level == "1ere" else profile.terminale_specialties.all()
    ) if profile else Subject.objects.none()
    if subject not in chosen:
        raise serializers.ValidationError(
            f"{subject.name} is a specialty subject you haven't selected for "
            f"{'Première' if level == '1ere' else 'Terminale'}. Update your specialties first."
        )


class GroupRequestSerializer(serializers.ModelSerializer):
    """
    Validates that a request for a SPECIALTY subject actually matches one of
    the student's chosen specialties for that level (spec: "chaque groupe
    doit être avec les mêmes spécialités sauf pour les matières de tronc
    commun"). COMMON_CORE subjects (Français, Philosophie, Histoire-Géo...)
    have no such restriction — every student takes them regardless of which
    specialties they picked, so those groups can freely mix students.

    `weekly_hours` (6/8/12/16/24) is the package the student is requesting
    — required for every group tier. INDIVIDUAL never goes through
    GroupRequest at all (see IndividualBookingSerializer), so this field
    doesn't apply there.
    """
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    weekly_hours_display = serializers.CharField(source="get_weekly_hours_display", read_only=True)
    resulting_enrollment_detail = EnrollmentSerializer(source="resulting_enrollment", read_only=True)

    class Meta:
        model = GroupRequest
        fields = [
            "id", "student", "subject", "subject_name", "level", "group_tier", "weekly_hours", "weekly_hours_display",
            "status", "group_assignment", "resulting_enrollment", "resulting_enrollment_detail", "created_at",
        ]
        read_only_fields = ["student", "status", "group_assignment", "resulting_enrollment", "created_at"]

    def validate(self, attrs):
        subject = attrs.get("subject", getattr(self.instance, "subject", None))
        level = attrs.get("level", getattr(self.instance, "level", None))
        weekly_hours = attrs.get("weekly_hours", getattr(self.instance, "weekly_hours", None))
        request = self.context.get("request")
        validate_specialty_access(subject, level, request.user if request else None)

        if not weekly_hours:
            raise serializers.ValidationError(
                {"weekly_hours": "Choisissez un forfait (4h/mois, 8h/mois, 6h, 8h, 12h, 16h ou 24h/semaine)."}
            )
        if weekly_hours not in WEEKLY_HOURS_PACKAGES:
            raise serializers.ValidationError(
                {"weekly_hours": f"Le forfait doit être l'un de : {WEEKLY_HOURS_PACKAGES}."}
            )
        return attrs


class GroupAssignmentSerializer(serializers.ModelSerializer):
    """
    A group formed by the admin and assigned to a teacher, still awaiting
    that teacher's schedule (see models.GroupAssignment). This is what
    populates the teacher dashboard's "Groupes à planifier" list.

    `weekly_hours` is the package's TOTAL weekly commitment — often more
    than one weekly slot is needed to reach it (e.g. 8h/semaine might be
    Monday 4h + Thursday 4h). `scheduled_slots` lists every slot the
    teacher has set up so far (each a ClassSeries), and
    `scheduled_weekly_minutes`/`target_weekly_minutes` let the dashboard
    show "X h programmées sur Y h/semaine" so the teacher knows exactly
    how many more slots they still need to add.
    """
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    weekly_hours_display = serializers.SerializerMethodField()
    teacher_name = serializers.CharField(source="teacher.user.get_full_name", read_only=True)
    student_count = serializers.SerializerMethodField()
    requests = GroupRequestSerializer(many=True, read_only=True)
    scheduled_slots = serializers.SerializerMethodField()
    target_weekly_minutes = serializers.SerializerMethodField()
    scheduled_weekly_minutes = serializers.SerializerMethodField()
    fully_scheduled = serializers.SerializerMethodField()

    class Meta:
        model = GroupAssignment
        fields = [
            "id", "subject", "subject_name", "level", "group_tier", "weekly_hours", "weekly_hours_display",
            "teacher", "teacher_name", "is_billable", "status",
            "scheduled_slots", "target_weekly_minutes", "scheduled_weekly_minutes", "fully_scheduled",
            "student_count", "requests", "created_at",
        ]
        read_only_fields = ["teacher", "is_billable", "status", "created_at"]

    def get_weekly_hours_display(self, obj):
        from .pricing import WEEKLY_HOURS_LABELS
        return WEEKLY_HOURS_LABELS.get(obj.weekly_hours) if obj.weekly_hours else None

    def get_student_count(self, obj):
        return obj.requests.count()

    def get_scheduled_slots(self, obj):
        return [
            {
                "id": s.id,
                "weekday": s.weekday,
                "weekday_display": s.get_weekday_display(),
                "start_time": s.start_time.isoformat(),
                "duration_minutes": s.duration_minutes,
                "ends_on": s.ends_on.isoformat(),
            }
            for s in obj.class_series.all().order_by("weekday", "start_time")
        ]

    def get_target_weekly_minutes(self, obj):
        return obj.weekly_hours * 60 if obj.weekly_hours else None

    def get_scheduled_weekly_minutes(self, obj):
        return sum(s.duration_minutes for s in obj.class_series.all())

    def get_fully_scheduled(self, obj):
        target = self.get_target_weekly_minutes(obj)
        if target is None:
            return obj.class_series.exists()
        return self.get_scheduled_weekly_minutes(obj) >= target


# ---------------------------------------------------------------------------
# Individual bookings — the INDIVIDUAL tier bypasses the group-request flow
# entirely (spec: "l'individuel est la seule formule où l'élève peut payer
# par séance et choisir n'importe quelle date/heure directement, car il ne
# dépend pas des groupes"). The student picks their own slot and pays
# immediately via Stripe; a teacher is assigned by the admin afterward
# (same "sessions à affecter" queue as any other session).
# ---------------------------------------------------------------------------
class IndividualBookingSerializer(serializers.Serializer):
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all())
    level = serializers.ChoiceField(choices=Subject.Level.choices)
    start_time = serializers.DateTimeField()
    end_time = serializers.DateTimeField()

    def validate(self, attrs):
        if attrs["end_time"] <= attrs["start_time"]:
            raise serializers.ValidationError("end_time must be after start_time.")
        duration_hours = (attrs["end_time"] - attrs["start_time"]).total_seconds() / 3600
        if not (0.25 <= duration_hours <= 4):
            raise serializers.ValidationError("A single individual session must be between 15 minutes and 4 hours.")
        request = self.context.get("request")
        validate_specialty_access(attrs["subject"], attrs["level"], request.user if request else None)
        return attrs


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
class MaterialSerializer(serializers.ModelSerializer):
    """
    Content a teacher shares with their students. Set exactly one of
    `group_assignment` (visible to the whole recurring group) or
    `class_session` (a single one-off session — INDIVIDUAL bookings and
    any session with no group). `content_type` decides which of
    `file`/`url` you must also provide — `document` needs `file`,
    `video_link` needs `url`.
    """
    uploaded_by_name = serializers.CharField(source="uploaded_by.get_full_name", read_only=True)
    subject_name = serializers.SerializerMethodField()

    class Meta:
        model = Material
        fields = [
            "id", "group_assignment", "class_session", "subject_name", "uploaded_by", "uploaded_by_name",
            "content_type", "title", "file", "url", "uploaded_at",
        ]
        read_only_fields = ["uploaded_by", "uploaded_at"]

    def get_subject_name(self, obj):
        if obj.group_assignment_id:
            return obj.group_assignment.subject.name
        if obj.class_session_id:
            return obj.class_session.subject.name
        return None

    def validate(self, attrs):
        group_assignment = attrs.get("group_assignment", getattr(self.instance, "group_assignment", None))
        class_session = attrs.get("class_session", getattr(self.instance, "class_session", None))
        if bool(group_assignment) == bool(class_session):
            raise serializers.ValidationError(
                "Set exactly one of group_assignment (for the whole group) or class_session (for a single session)."
            )

        content_type = attrs.get("content_type", getattr(self.instance, "content_type", Material.ContentType.DOCUMENT))
        file = attrs.get("file", getattr(self.instance, "file", None))
        url = attrs.get("url", getattr(self.instance, "url", ""))
        if content_type == Material.ContentType.DOCUMENT and not file:
            raise serializers.ValidationError({"file": "A file is required for content_type=document."})
        if content_type == Material.ContentType.VIDEO_LINK and not url:
            raise serializers.ValidationError({"url": "A url is required for content_type=video_link."})
        return attrs


class GroupAnnouncementSerializer(serializers.ModelSerializer):
    """A message a teacher posts for every student in one of their groups — see models.GroupAnnouncement."""
    author_name = serializers.CharField(source="author.get_full_name", read_only=True)
    subject_name = serializers.CharField(source="group_assignment.subject.name", read_only=True)

    class Meta:
        model = GroupAnnouncement
        fields = ["id", "group_assignment", "author", "author_name", "subject_name", "message", "created_at"]
        read_only_fields = ["author", "created_at"]


# ---------------------------------------------------------------------------
# Forum
# ---------------------------------------------------------------------------
class ForumReplySerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="user.get_full_name", read_only=True)
    author_role = serializers.CharField(source="user.role", read_only=True)

    class Meta:
        model = ForumReply
        fields = ["id", "thread", "user", "author_name", "author_role", "body", "is_accepted_answer", "created_at"]
        read_only_fields = ["user", "created_at"]


class ForumThreadSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="user.get_full_name", read_only=True)
    reply_count = serializers.IntegerField(source="replies.count", read_only=True)

    class Meta:
        model = ForumThread
        fields = ["id", "user", "author_name", "subject", "level", "title", "body", "is_solved", "reply_count", "created_at"]
        read_only_fields = ["user", "created_at"]


# ---------------------------------------------------------------------------
# Landing page (public, unauthenticated)
# ---------------------------------------------------------------------------
class PublicTeacherSerializer(serializers.ModelSerializer):
    """
    Lean, public-facing teacher card for the landing page's "Nos
    enseignants experts" section — deliberately exposes far less than
    TeacherProfileSerializer (no bio, no subjects list, no settings).
    """
    full_name = serializers.CharField(source="user.get_full_name", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)

    class Meta:
        model = TeacherProfile
        fields = ["id", "full_name", "photo", "subject_name", "title_degree", "bio_short"]


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ["id", "question", "response", "order", "is_visible"]


class StaticPageSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaticPage
        fields = ["slug", "title", "content", "updated_at"]


class NewsletterSubscriberSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsletterSubscriber
        fields = ["email"]
