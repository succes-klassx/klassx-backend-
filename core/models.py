"""
Core data models for KLASSX.

Covers the blueprint from the technical specification (section 6) plus the
extra tables the spec's own "Business Rules & Edge Cases" section (5) calls
for: Payments, TeacherAvailability, Materials, Payouts, and a lightweight
ClassSeries model to support recurring weekly classes.

Design choices made here (should be confirmed with the product owner):
- All datetimes are timezone-aware and stored in UTC (spec 5.7).
- A class can belong to a ClassSeries to model weekly recurrence (spec 5.5).
  A one-off booking simply has series=None.
- Enrollment has a `waitlisted` flag instead of a separate table, since a
  waitlist entry is just an enrollment that hasn't been offered a seat yet
  (spec 5.1).
- Refunds are modeled as a status + reason on Payment rather than a separate
  table, since KLASSX's refund logic is simple (full/partial/none).
"""
from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone


# ---------------------------------------------------------------------------
# Users & profiles
# ---------------------------------------------------------------------------
class User(AbstractUser):
    """Custom user model so `role` is available without a profile join."""

    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        TEACHER = "teacher", "Teacher"
        ADMIN = "admin", "Admin"
        AFFILIATE = "affiliate", "Affiliate"

    # AbstractUser's email is blank=True and NOT unique by default — every
    # registration serializer already checks for a duplicate email before
    # creating the account (see StudentRegistrationSerializer/
    # TeacherRegistrationSerializer.validate_email), but that's an
    # application-level check-then-create, not a DB guarantee: two
    # concurrent registrations for the same address could both pass the
    # check before either has saved (a race condition). This constraint
    # closes that gap — the second save() would raise IntegrityError,
    # which DRF surfaces as a clean 400, not a 500.
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STUDENT)
    phone = models.CharField(max_length=30, blank=True)
    country = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- Programme de parrainage (ouvert à tout compte, pas seulement aux
    # enseignants — un affilié pur passe par Role.AFFILIATE ci-dessus).
    # `referral_code` est généré automatiquement à la création du compte
    # (voir save() ci-dessous) ; `referred_by` est renseigné une seule fois,
    # à l'inscription, si un code valide était présent dans le lien utilisé
    # (voir RegisterView/AffiliateRegisterView) — jamais modifié après coup.
    referral_code = models.CharField(max_length=20, unique=True, blank=True)
    referred_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals"
    )

    def save(self, *args, **kwargs):
        if not self.referral_code:
            self.referral_code = self._generate_referral_code()
        super().save(*args, **kwargs)

    def _generate_referral_code(self):
        import random
        import string

        base = "".join(c for c in (self.last_name or self.username or "USER").upper() if c.isalnum())[:10] or "USER"
        for _ in range(20):
            candidate = f"{base}{''.join(random.choices(string.digits, k=4))}"
            if not User.objects.filter(referral_code=candidate).exists():
                return candidate
        # Extrêmement improbable après 20 essais, mais ne doit jamais planter
        # une inscription pour autant — repli sur un suffixe long et unique.
        return f"{base}{''.join(random.choices(string.ascii_uppercase + string.digits, k=8))}"

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.role})"


class BacType(models.TextChoices):
    """
    Les parcours proposés sur KLASSX — shared between StudentProfile.bac_type
    and Subject.bac_type (module-level so neither model has to be defined
    before the other to reference it). The subject catalog differs
    significantly between tracks: a "Bac Général" student picks
    specialties from one list; "Bac Technologique" follows a fixed
    curriculum specific to their série (STMG, STI2D, ST2S...); "Bac
    Professionnel" follows their filière's own program.

    FLE/FLS ne sont PAS des filières du Bac — le nom du champ reste
    "bac_type" pour ne pas casser tout le code existant qui le référence
    (validate_specialty_access, Catalog.jsx, etc.), mais elles réutilisent
    exactement le même mécanisme de filtrage de catalogue par "parcours".
    Un élève FLE/FLS n'a ni Première/Terminale ni spécialités — voir
    StudentProfile.cecrl_level (leur équivalent de grade_level) et
    validate_specialty_access, qui désactive déjà tout le système de
    spécialités dès que bac_type != GENERAL (aucune modification requise
    là pour que FLE/FLS fonctionnent correctement).
    """
    GENERAL = "general", "Général"
    TECHNOLOGIQUE = "techno", "Technologique"
    PROFESSIONNEL = "pro", "Professionnel"
    FLE = "fle", "FLE — Français Langue Étrangère"
    FLS = "fls", "FLS — Français Langue Seconde"


class CecrlLevel(models.TextChoices):
    """
    Niveaux du Cadre européen commun de référence pour les langues —
    utilisé par Subject.cecrl_level (quel niveau enseigne cette matière
    FLE/FLS) et StudentProfile.cecrl_level (à quel niveau se situe
    l'élève). Sans rapport avec Subject.Level (Première/Terminale), qui
    ne s'applique pas aux parcours FLE/FLS.
    """
    A1 = "A1", "A1 — Découverte"
    A2 = "A2", "A2 — Survie"
    B1 = "B1", "B1 — Seuil"
    B2 = "B2", "B2 — Avancé"
    C1 = "C1", "C1 — Autonome"
    C2 = "C2", "C2 — Maîtrise"


class StudentProfile(models.Model):
    class GradeLevel(models.TextChoices):
        PREMIERE = "1ere", "1ère"
        TERMINALE = "terminale", "Terminale"

    class CandidateType(models.TextChoices):
        STANDARD = "standard", "Standard high school"
        CANDIDAT_LIBRE = "candidat_libre", "Candidat libre"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="student_profile")
    bac_type = models.CharField(max_length=10, choices=BacType.choices, default=BacType.GENERAL)
    # blank=True — sans objet pour un élève FLE/FLS (pas de Première/
    # Terminale dans ce parcours) ; obligatoire uniquement pour
    # Général/Techno/Pro, vérifié dans les serializers (validate()).
    grade_level = models.CharField(max_length=10, choices=GradeLevel.choices, blank=True)
    # Niveau CECRL — l'équivalent de grade_level pour un élève FLE/FLS,
    # sans rapport avec Première/Terminale. Sans objet pour
    # Général/Techno/Pro. Voir CecrlLevel.
    cecrl_level = models.CharField(max_length=2, choices=CecrlLevel.choices, blank=True)
    candidate_type = models.CharField(max_length=20, choices=CandidateType.choices, default=CandidateType.STANDARD)
    # Specialty choices (spec: French Bac Général reform — students pick up
    # to 3 specialties in Première and keep 2 of them in Terminale, which
    # can differ between the two years). Only meaningful for
    # bac_type=GENERAL — Technologique/Professionnel students follow a
    # fixed curriculum for their série/filière instead of picking
    # specialties, so these normally stay empty for them (see Catalog.jsx,
    # which skips the specialty gate entirely for non-Général students).
    # Only Subject rows with subject_type=SPECIALTY should ever appear
    # here — enforced in serializers, not at the DB level, since M2M can't
    # carry a queryset constraint in the model itself.
    premiere_specialties = models.ManyToManyField(
        "Subject", blank=True, related_name="students_specialty_1ere",
        help_text="Up to 3 specialty subjects chosen for Première.",
    )
    terminale_specialties = models.ManyToManyField(
        "Subject", blank=True, related_name="students_specialty_terminale",
        help_text="Up to 2 specialty subjects kept for Terminale.",
    )
    # "Option mathématiques" de Terminale — Maths Expertes (si Mathématiques
    # fait partie des 2 spécialités conservées ci-dessus) OU Maths
    # Complémentaires (si Mathématiques a été abandonnée) — jamais les
    # deux à la fois : c'est un choix mutuellement exclusif, d'où un FK
    # simple plutôt qu'un ManyToMany comme les spécialités. NE compte PAS
    # dans le plafond de 2 spécialités Terminale — c'est un enseignement
    # séparé, pas une 3e spécialité (voir Subject.SubjectType.MATH_OPTION).
    # La cohérence avec terminale_specialties (Expertes exige Mathématiques
    # gardée, Complémentaires exige qu'elle soit abandonnée) est vérifiée
    # dans les serializers, pas ici — voir validate_specialty_access.
    terminale_math_option = models.ForeignKey(
        "Subject", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="students_math_option",
        limit_choices_to={"subject_type": "math_option"},
        help_text="Maths Expertes ou Maths Complémentaires, au choix (Terminale uniquement).",
    )
    # Displayed session times are converted to this timezone client-side or
    # server-side depending on the client (spec 5.7). IANA name, e.g. "Europe/Paris".
    timezone = models.CharField(max_length=64, default="Europe/Paris")
    # ------------------------------------------------------------------
    # Parental consent (RGPD/Loi 25 — most students here are lycéens,
    # commonly minors). date_of_birth drives is_minor/requires_parental_consent
    # below; nullable only so accounts created before this field existed
    # don't break — every new registration collects it (see
    # StudentRegistrationSerializer). KLASSX uses 18 as a single, uniform
    # threshold rather than a lower one (e.g. Québec's Loi 25 lets a minor
    # of 14+ consent to data collection themselves, and French law uses 15
    # for information-society consent specifically) — chosen because a
    # minor of any age still can't independently enter the binding payment
    # contract a subscription is. KLASSX serves students in several
    # jurisdictions, so confirm this choice (and the whole flow below)
    # with counsel in each relevant jurisdiction before relying on it;
    # this is a technical scaffold, not a legal opinion.
    # ------------------------------------------------------------------
    date_of_birth = models.DateField(null=True, blank=True)
    # ------------------------------------------------------------------
    # Saved-card billing model: a student enters their card right when
    # requesting a group package, but isn't actually charged until their
    # teacher schedules a real session — see
    # core/services/payments.py: create_card_setup_checkout_session /
    # charge_saved_payment_method, and GroupAssignmentViewSet.schedule.
    # Both blank until the student completes the Stripe "setup" Checkout
    # session (captured via the checkout.session.completed webhook, kind
    # "card_setup").
    # ------------------------------------------------------------------
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    stripe_default_payment_method_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def has_payment_method(self):
        return bool(self.stripe_default_payment_method_id)

    @property
    def age(self):
        """Age in whole years today, or None if date_of_birth wasn't collected (pre-existing accounts)."""
        if not self.date_of_birth:
            return None
        today = timezone.localdate()
        had_birthday_this_year = (today.month, today.day) >= (self.date_of_birth.month, self.date_of_birth.day)
        return today.year - self.date_of_birth.year - (0 if had_birthday_this_year else 1)

    @property
    def is_minor(self):
        age = self.age
        return age is not None and age < 18

    @property
    def requires_parental_consent(self):
        """
        True when this student is a minor AND we don't have a confirmed
        ParentalConsent on file yet. Gate anything that creates a real
        payment method or charge on this being False — see
        PaymentMethodSetupView / EnrollmentViewSet.create_checkout_session.
        Deliberately does NOT gate browsing the catalog or filing a
        GroupRequest — those don't move money yet.
        """
        if not self.is_minor:
            return False
        consent = getattr(self, "parental_consent", None)
        return consent is None or consent.status != ParentalConsent.Status.CONFIRMED

    def __str__(self):
        return f"Student profile: {self.user}"


class ParentalConsent(models.Model):
    """
    RGPD / preuve de consentement parental pour un élève mineur — voir
    StudentProfile.date_of_birth's docstring pour le seuil d'âge choisi.

    Créé automatiquement à l'inscription quand la date de naissance
    indique un mineur (voir StudentRegistrationSerializer.create). Le
    compte d'un mineur est un compte UNIQUE, partagé — le formulaire
    d'inscription demande alors l'email et le mot de passe du PARENT (qui
    deviennent les identifiants de connexion du compte) ainsi que le nom
    du parent ET celui de l'élève : c'est cet acte conjoint (parent et
    élève inscrivent le compte ensemble, avec un mot de passe choisi en
    commun) qui vaut consentement — status passe directement à CONFIRMED
    à la création, sans étape supplémentaire ni lien par email à cliquer.
    confirmed_ip est conservé comme preuve (l'IP au moment de
    l'inscription), pas comme donnée sensible sur l'élève.

    Pas un avis juridique : confirmer avec un juriste que ce mécanisme
    (seuil d'âge uniforme à 18 ans, mot de passe conjoint comme preuve de
    consentement, ce que ça débloque) suffit aux obligations de KLASSX
    avant de s'appuyer dessus en production.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        CONFIRMED = "confirmed", "Confirmé"

    student = models.OneToOneField(StudentProfile, on_delete=models.CASCADE, related_name="parental_consent")
    parent_full_name = models.CharField(max_length=150)
    # Même adresse que le login du compte pour un élève mineur (voir
    # docstring ci-dessus) — dupliquée ici plutôt que dérivée de
    # student.user.email à la volée, pour garder une trace même si
    # l'email de connexion est modifié plus tard.
    parent_email = models.EmailField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CONFIRMED)
    requested_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_ip = models.GenericIPAddressField(null=True, blank=True)

    def __str__(self):
        return f"Consentement parental pour {self.student} ({self.get_status_display()})"


class TeacherProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_profile")
    bio = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)  # flips to True once admin-approved
    # Compensation model referenced in spec 5.4 — kept simple; a per-teacher
    # override table could replace this if rates vary by subject/tier.
    compensation_type = models.CharField(
        max_length=20,
        choices=[("flat_per_session", "Flat per session"), ("per_student", "Per student present"), ("percentage", "Percentage of revenue")],
        default="flat_per_session",
    )
    compensation_rate = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    # ------------------------------------------------------------------
    # Teacher-owned video conferencing (autonomous scheduling model): each
    # teacher is responsible for the meeting link on the sessions they
    # schedule themselves — see core/services/video.py resolution order.
    # Two options, either is enough:
    # 1. `default_meeting_url` — a personal, reusable link the teacher
    #    pastes once (e.g. their own permanent Google Meet room, Zoom
    #    personal meeting room, Teams link...). Simplest, works with any
    #    provider.
    # 2. Connect their own Google account (`google_oauth_refresh_token`) —
    #    KLASSX then creates a fresh, real Google Calendar event + Meet
    #    link on the teacher's own calendar for every session they
    #    schedule, same mechanism as the org-wide integration but scoped
    #    to this one teacher. See views.TeacherGoogleConnectView.
    # If both are set, the Google connection takes priority (a real
    # generated link beats a static pasted one) — see video.py.
    # ------------------------------------------------------------------
    default_meeting_url = models.URLField(
        blank=True,
        help_text="Lien de visioconférence personnel (Google Meet, Zoom, Teams...), réutilisé par défaut pour "
                   "les séances que cet enseignant planifie lui-même.",
    )
    google_account_email = models.EmailField(blank=True, help_text="Set automatically once the teacher connects their Google account.")
    # Never exposed via the API — see TeacherProfileSerializer.
    google_oauth_refresh_token = models.CharField(max_length=255, blank=True)
    # ------------------------------------------------------------------
    # Public display fields (landing page "Nos enseignants experts"
    # section) — see PublicTeacherSerializer. `is_featured` teachers are
    # the ones shown there; the rest of these fields are only meaningful
    # once a teacher is featured, but any teacher can have them filled in
    # ahead of time.
    # ------------------------------------------------------------------
    photo = models.ImageField(upload_to="teacher_photos/%Y/%m/", blank=True)
    subject = models.ForeignKey(
        "Subject", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Matière principale mise en avant sur la landing page (ex: Mathématiques).",
    )
    title_degree = models.CharField(
        max_length=200, blank=True,
        help_text="Diplôme ou titre affiché publiquement, ex: \"Doctorante en Physique\", \"17 ans d'exp. Éducation Nationale\".",
    )
    bio_short = models.CharField(
        max_length=300, blank=True, help_text="Phrase d'accroche / citation courte affichée sur la carte enseignant.",
    )
    is_featured = models.BooleanField(default=False, help_text="Afficher cet enseignant sur la page d'accueil.")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Teacher profile: {self.user}"

    @property
    def google_connected(self):
        return bool(self.google_oauth_refresh_token)


class TeacherAvailability(models.Model):
    """Recurring weekly availability windows, used by the admin when assigning teachers."""

    WEEKDAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
                (4, "Friday"), (5, "Saturday"), (6, "Sunday")]

    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="availabilities")
    weekday = models.IntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.teacher} - {self.get_weekday_display()} {self.start_time}-{self.end_time}"


# ---------------------------------------------------------------------------
# Subjects & teacher-subject mapping
# ---------------------------------------------------------------------------
class Subject(models.Model):
    class Level(models.TextChoices):
        PREMIERE = "1ere", "1ère"
        TERMINALE = "terminale", "Terminale"
        BOTH = "both", "Both"

    class SubjectType(models.TextChoices):
        # Tronc commun: taken by every student regardless of which
        # specialties they picked (Français, Philosophie, Histoire-Géo,
        # Anglais, EPS, Enseignement scientifique...) — groups for these
        # subjects can freely mix students with different specialty
        # combinations.
        COMMON_CORE = "common_core", "Tronc commun"
        # Spécialité: only taken by students who chose it (e.g. Maths,
        # Physique-Chimie, SES, HGGSP, LLCE...) — a GroupRequest for one of
        # these is only allowed if the student actually picked it as one of
        # their specialties for that level (see GroupRequestSerializer).
        # Only meaningful for BacType.GENERAL — Technologique/Professionnel
        # students don't "pick" specialties the same way, so their subjects
        # are all effectively common_core in practice (see
        # StudentProfile.bac_type / Catalog filtering on the frontend).
        SPECIALTY = "specialty", "Spécialité"
        # "Enseignement optionnel" de Terminale, PAS une 3e spécialité :
        # Mathématiques Expertes (en plus des 2 spécialités conservées, si
        # Mathématiques en fait partie) et Mathématiques Complémentaires
        # (à la place de Mathématiques abandonnée en spécialité). Les deux
        # sont mutuellement exclusives et ne comptent jamais dans le
        # plafond de 2 spécialités en Terminale — voir
        # StudentProfile.terminale_math_option.
        MATH_OPTION = "math_option", "Option mathématiques (Terminale)"

    name = models.CharField(max_length=100)
    code = models.SlugField(max_length=30, unique=True)
    # Sans objet pour bac_type=FLE/FLS (voir cecrl_level ci-dessous à la
    # place) — laissé à Level.BOTH par défaut pour ces matières, pour ne
    # jamais bloquer une réservation sur un contrôle Première/Terminale
    # qui n'a pas de sens pour elles (voir validate_specialty_access).
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.BOTH)
    bac_type = models.CharField(max_length=10, choices=BacType.choices, default=BacType.GENERAL)
    subject_type = models.CharField(max_length=15, choices=SubjectType.choices, default=SubjectType.SPECIALTY)
    # Niveau CECRL enseigné par cette matière — uniquement pour
    # bac_type=FLE/FLS (ex: une matière "FLE" par niveau A1 à C2). Sans
    # objet pour Général/Techno/Pro, laissé vide.
    cecrl_level = models.CharField(max_length=2, choices=CecrlLevel.choices, blank=True)
    # Official French Bac Général weekly hours (Éducation nationale grille
    # horaire), used only to compute a *reference* monthly price shown to
    # families for a full-course commitment — not the actual price of a
    # scheduled group, which depends on how many hours per week the admin
    # actually books (see core/pricing.py). Specialties are 4h/week in
    # Première and 6h/week in Terminale for every specialty; tronc commun
    # hours vary per subject (e.g. Français 4h, Histoire-Géo 3h, Langues 2h).
    # Leave blank for subjects without a reference (e.g. Grand Oral, sold as
    # a flat coaching package instead — see pricing.py docstring).
    hours_per_week_premiere = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    hours_per_week_terminale = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TeacherSubject(models.Model):
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="subjects")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="teachers")

    class Meta:
        unique_together = ("teacher", "subject")

    def __str__(self):
        return f"{self.teacher} teaches {self.subject}"


# ---------------------------------------------------------------------------
# Video capsules (Maths only, on-demand)
# ---------------------------------------------------------------------------
class SelfStudyPlan(models.Model):
    """
    Un des 6 abonnements de contenu en libre-service (vidéos + PDF, SANS
    accompagnement par un enseignant — à ne pas confondre avec
    SeriesMembership, qui est un forfait de cours EN DIRECT avec un
    enseignant). Chaque plan est complètement indépendant des autres :
    un élève peut s'abonner à un, plusieurs, ou tous les 6, chacun étant
    facturé séparément (voir Subscription, qui a une ligne par (élève,
    plan), pas juste par élève).

    Les 6 plans sont fixes (spec) : seed-és une fois via
    `python manage.py seed_selfstudy_plans` — modifiables ensuite depuis
    l'admin (ex: changer le prix) sans déploiement de code.
    """
    class MathTrack(models.TextChoices):
        PREMIERE_NON_SPE = "premiere_non_spe", "1ère — Tronc commun (non spécialité)"
        PREMIERE_SPE = "premiere_spe", "1ère — Spécialité Mathématiques"
        PREMIERE_TECHNO = "premiere_techno", "1ère Technologique"
        TERMINALE_SPE = "terminale_spe", "Terminale — Spécialité Mathématiques"
        TERMINALE_MATHS_EXPERTES = "terminale_maths_expertes", "Terminale — Mathématiques Expertes"
        TERMINALE_MATHS_COMPLEMENTAIRES = "terminale_maths_complementaires", "Terminale — Mathématiques Complémentaires"

    code = models.CharField(max_length=35, choices=MathTrack.choices, unique=True)
    name = models.CharField(max_length=150)
    price_cents = models.PositiveIntegerField(default=499, help_text="Prix mensuel en centimes d'euro (défaut : 4,99€).")
    # Tunisie : prix indépendant du prix EUR ci-dessus (comme pour les
    # cours — voir core/pricing.py), pas une conversion de change. Payé
    # par virement bancaire manuel (voir SubscriptionCheckoutView), pas de
    # Konnect ici : aucun prestataire de paiement tunisien fiable pour
    # l'instant. Modifiable depuis l'admin sans déploiement de code.
    price_millimes_tnd = models.PositiveIntegerField(
        default=7000, help_text="Prix mensuel en millimes tunisiens (1 DT = 1000 millimes). Défaut : 7 DT."
    )
    is_active = models.BooleanField(default=True, help_text="Décoché = plus proposé à l'abonnement (les abonnés existants gardent leur accès).")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.name} — {self.price_cents / 100:.2f}€/mois"


class SelfStudyContentItem(models.Model):
    """
    Une vidéo ou un PDF appartenant à un SelfStudyPlan, publié pour un
    mois donné. `is_unlocked` est le mécanisme demandé : un admin ne le
    coche PAS à la création (le contenu peut être préparé à l'avance,
    caché) puis le coche quand il veut le rendre visible aux abonnés
    actifs de ce plan — voir SelfStudyContentViewSet côté API, qui ne
    sert jamais un item avec is_unlocked=False à un élève, quel que soit
    son abonnement.

    "Pour les personnes qui renouvellent" (spec) : l'accès est vérifié en
    temps réel sur l'abonnement (Subscription.status=ACTIVE), pas figé au
    moment de l'achat — un élève qui annule perd l'accès au contenu déjà
    débloqué, y compris celui des mois précédents ; un élève qui
    réabonne après une pause le retrouve immédiatement. C'est le
    fonctionnement standard d'un abonnement de contenu (Netflix-like),
    pas un achat à la pièce.
    """
    class ContentType(models.TextChoices):
        VIDEO = "video", "Vidéo"
        PDF = "pdf", "PDF"

    plan = models.ForeignKey(SelfStudyPlan, on_delete=models.CASCADE, related_name="content_items")
    content_type = models.CharField(max_length=5, choices=ContentType.choices)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    chapter_name = models.CharField(max_length=150, blank=True)
    # Premier jour du mois concerné (ex: 2026-09-01 pour "le contenu de
    # septembre") — permet de grouper/trier par mois côté élève et admin.
    month = models.DateField(help_text="Premier jour du mois concerné, ex : 2026-09-01 pour le contenu de septembre.")
    is_unlocked = models.BooleanField(default=False, help_text="Visible par les abonnés actifs de ce plan une fois coché.")
    order_index = models.PositiveIntegerField(default=0)
    # Vidéo : ID/URL chez le prestataire de streaming (ex: Cloudflare
    # Stream UID) — même convention que l'ancien VideoCapsule.
    video_provider_id = models.CharField(max_length=200, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    # PDF : fichier uploadé directement (voir MEDIA_ROOT/MEDIA_URL).
    pdf_file = models.FileField(upload_to="selfstudy_pdfs/%Y/%m/", blank=True)

    class Meta:
        ordering = ["plan", "month", "chapter_name", "order_index"]

    def __str__(self):
        return f"{self.plan} — {self.month:%Y-%m} — {self.title}"


class VideoProgress(models.Model):
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="video_progress")
    capsule = models.ForeignKey(SelfStudyContentItem, on_delete=models.CASCADE, related_name="progress_entries")
    progress_percentage = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("student", "capsule")

    def __str__(self):
        return f"{self.student} - {self.capsule}: {self.progress_percentage}%"


# ---------------------------------------------------------------------------
# Group requests — new booking model (confirmed with product owner):
# students no longer pick a time slot. They express interest in a subject +
# level + group size, and the admin later assembles a group of matching
# requests, decides the schedule, assigns a teacher, and creates the actual
# ClassSession — which becomes a fixed, recurring group for the rest of the
# term, so a teacher always sees the same students and knows what's been
# covered (this is the whole point: no more ad-hoc mixing of students).
# ---------------------------------------------------------------------------
class GroupRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"       # waiting to be grouped/scheduled
        TEACHER_ASSIGNED = "teacher_assigned", "Assigned to a teacher, awaiting their schedule"
        SCHEDULED = "scheduled", "Scheduled"  # teacher (or admin) created a session for it
        CANCELLED = "cancelled", "Cancelled"

    class GroupTier(models.TextChoices):
        GROUP_10 = "GROUP_10", "Group of 10"
        GROUP_8 = "GROUP_8", "Group of 8"
        GROUP_6 = "GROUP_6", "Group of 6"
        GROUP_5 = "GROUP_5", "Group of 5"
        GROUP_4 = "GROUP_4", "Group of 4"
        GROUP_3 = "GROUP_3", "Group of 3"
        GROUP_2 = "GROUP_2", "Group of 2"
        INDIVIDUAL = "INDIVIDUAL", "Individual"

    # Monthly hour packages (spec: confirmed with product owner). Only
    # meaningful for group tiers; INDIVIDUAL has no package — it's pay-per-
    # session with no commitment and never goes through this request flow
    # at all (see IndividualBookingSerializer).
    #
    # The value stored here is the package's TOTAL MONTHLY hours (not
    # weekly) — see GroupAssignmentSerializer.get_target_weekly_minutes,
    # which divides by 4 to get the actual weekly scheduling target. See
    # also core/pricing.py: WEEKLY_HOURS_LABELS.
    class WeeklyHours(models.IntegerChoices):
        H4 = 4, "1h/semaine (4h/mois)"
        H6 = 6, "1,5h/semaine (6h/mois)"
        H8 = 8, "2h/semaine (8h/mois)"
        H12 = 12, "3h/semaine (12h/mois)"
        H16 = 16, "4h/semaine (16h/mois)"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="group_requests")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="group_requests")
    level = models.CharField(max_length=10, choices=Subject.Level.choices)
    group_tier = models.CharField(max_length=12, choices=GroupTier.choices)
    weekly_hours = models.PositiveSmallIntegerField(choices=WeeklyHours.choices, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Optional — set when the student clicked "Réserver un cours avec
    # <nom>" from that teacher's public profile page (see
    # TeacherDetail.jsx), so an admin knows there's a preference before
    # assigning a teacher via AdminAssignGroupView. Purely informational:
    # nothing here auto-assigns the teacher, and this field stays null
    # for the (still more common) case of a student who just picked a
    # subject/level without visiting a specific teacher's page first.
    preferred_teacher = models.ForeignKey(
        "TeacherProfile", on_delete=models.SET_NULL, null=True, blank=True, related_name="preferred_by_requests"
    )
    # Set once an admin bundles this request into a formed group and
    # assigns a teacher to it (see AdminAssignGroupView) — the teacher then
    # picks the actual schedule from their dashboard (see
    # GroupAssignmentViewSet.schedule), which is what eventually populates
    # resulting_enrollment below.
    group_assignment = models.ForeignKey(
        "GroupAssignment", on_delete=models.SET_NULL, null=True, blank=True, related_name="requests"
    )
    # Set once a session actually exists for this request (either via the
    # teacher's schedule() action, or — legacy — a direct admin schedule).
    resulting_enrollment = models.ForeignKey(
        "Enrollment", on_delete=models.SET_NULL, null=True, blank=True, related_name="source_requests"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.student} wants {self.subject} ({self.get_group_tier_display()}) - {self.status}"


# ---------------------------------------------------------------------------
# Live class sessions
# ---------------------------------------------------------------------------
class GroupAssignment(models.Model):
    """
    A formed group (a bundle of matching GroupRequests, same subject/level/
    group size/weekly-hour package) with a teacher assigned to it — created
    by the admin (AdminAssignGroupView) — but with NO schedule yet. The
    teacher picks the actual weekday(s)/time(s)/recurrence themselves from
    their dashboard (see GroupAssignmentViewSet.schedule), which is what
    finally creates the real ClassSeries/ClassSession(s) and enrolls the
    students. This is the autonomous-scheduling model: the admin's job
    stops at "who teaches this group", the teacher's job is "when do we
    meet, and what's the link".

    IMPORTANT: `weekly_hours` is the TOTAL weekly commitment of the package
    (e.g. 8h/semaine), which often needs MORE THAN ONE weekly time slot to
    reach (e.g. Monday 4h + Thursday 4h) — a single class rarely runs for
    the full weekly hour count in one sitting. `schedule()` can be called
    more than once (or with several slots in one call) to add each slot;
    see ClassSeries.group_assignment / ClassSession.group_assignment for
    how they all link back here, and GroupAssignmentSerializer for the
    "how many hours have I actually scheduled so far" figure shown on the
    teacher's dashboard.
    """
    class Status(models.TextChoices):
        AWAITING_SCHEDULE = "awaiting_schedule", "Awaiting teacher's schedule"
        SCHEDULED = "scheduled", "At least one slot scheduled"

    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="group_assignments")
    level = models.CharField(max_length=10, choices=Subject.Level.choices)
    group_tier = models.CharField(max_length=12, choices=[
        ("GROUP_10", "Group of 10"), ("GROUP_8", "Group of 8"), ("GROUP_6", "Group of 6"), ("GROUP_5", "Group of 5"),
        ("GROUP_4", "Group of 4"), ("GROUP_3", "Group of 3"), ("GROUP_2", "Group of 2"), ("INDIVIDUAL", "Individual"),
    ])
    weekly_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="group_assignments")
    # Whether the package should be billed at all once fully scheduled —
    # False for an *additional* slot of a package already billed elsewhere
    # (see SeriesMembership.is_billable). Only ever affects the FIRST slot
    # ever created for this assignment; every slot after that is always
    # forced non-billable regardless of this value, to avoid charging the
    # same package's monthly price more than once — see
    # GroupAssignmentViewSet.schedule.
    is_billable = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_SCHEDULE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.get_group_tier_display()}) -> {self.teacher} [{self.status}]"


class GroupAnnouncement(models.Model):
    """
    A message a teacher posts for every student in one of their groups —
    shown on the student dashboard, most recent first. Scoped to
    GroupAssignment (not individual sessions) since announcements are
    inherently a group-wide thing — an INDIVIDUAL booking has no group to
    announce to.
    """
    group_assignment = models.ForeignKey(GroupAssignment, on_delete=models.CASCADE, related_name="announcements")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="group_announcements"
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.group_assignment} — {self.message[:40]}"


class ClassSeries(models.Model):
    """
    Groups together recurring weekly sessions that share the same group,
    subject and (usually) teacher — spec 5.5. A one-off session simply has
    no ClassSeries and books ClassSession directly.
    """
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="series")
    level = models.CharField(max_length=10, choices=Subject.Level.choices)
    group_tier = models.CharField(max_length=12, choices=[
        ("GROUP_10", "Group of 10"), ("GROUP_8", "Group of 8"), ("GROUP_6", "Group of 6"), ("GROUP_5", "Group of 5"),
        ("GROUP_4", "Group of 4"), ("GROUP_3", "Group of 3"), ("GROUP_2", "Group of 2"), ("INDIVIDUAL", "Individual"),
    ])
    # The weekly-hour package this series bills for (spec: 6/8/12/16/24h
    # packages, confirmed with product owner). A package can span more than
    # one weekly time slot (e.g. an 8h/week package might be Monday 4h +
    # Thursday 4h) — in that case, each slot is its own ClassSeries with
    # the SAME weekly_hours value and the SAME group_assignment, but only
    # ONE of the resulting SeriesMembership rows is billable (see
    # SeriesMembership.is_billable) so the family isn't charged twice for
    # one package. GroupAssignmentViewSet.schedule() handles this
    # automatically when the teacher adds more than one slot.
    weekly_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    weekday = models.IntegerField(choices=TeacherAvailability.WEEKDAYS)
    start_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    starts_on = models.DateField()
    ends_on = models.DateField()
    assigned_teacher = models.ForeignKey(
        TeacherProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_series"
    )
    # Set when this series was created via the autonomous scheduling flow
    # (GroupAssignmentViewSet.schedule) — lets the teacher dashboard find
    # every slot belonging to the same package and add up their durations
    # against `weekly_hours` above. Blank for series created the old way
    # (AdminScheduleGroupView, the legacy manual-override path).
    group_assignment = models.ForeignKey(
        GroupAssignment, on_delete=models.SET_NULL, null=True, blank=True, related_name="class_series"
    )

    def __str__(self):
        return f"{self.subject} series ({self.get_group_tier_display()})"


class SeriesMembership(models.Model):
    """
    A student's ongoing package subscription for one subject (billed
    monthly, auto-renewing — confirmed with product owner). Changes or
    cancellations always take effect at the **start of the next calendar
    month**: the current month's package always runs to completion
    unchanged ("le groupe doit terminer le forfait mensuel sans aucun
    changement"). This only applies to group-tier packages — the
    INDIVIDUAL plan is pay-per-session with no commitment and has no
    membership row at all (see IndividualBookingSerializer).
    """
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        LEAVING = "leaving", "Leaving (ends this month)"
        LEFT = "left", "Left"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="series_memberships")
    series = models.ForeignKey(ClassSeries, on_delete=models.CASCADE, related_name="memberships")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    monthly_price_cents = models.PositiveIntegerField(help_text="Snapshot of the monthly rate at signup, in cents.")
    # False when this membership represents an *additional* weekly slot of
    # a package that's already billed through another SeriesMembership for
    # the same student+subject (e.g. the Thursday half of an 8h/week
    # package whose Monday half already carries the charge) — set manually
    # by the admin when scheduling a multi-slot package. True = this row is
    # actually charged.
    is_billable = models.BooleanField(default=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    leave_requested_at = models.DateTimeField(null=True, blank=True)
    # Always the 1st of the month following the request — see save()/leave
    # action. Named "leaves_on" for continuity but now month-aligned rather
    # than a rolling 14-day window.
    leaves_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("student", "series")

    def __str__(self):
        return f"{self.student} in {self.series} ({self.status})"

    def is_member_on(self, moment):
        """Whether this membership still counts as active at a given datetime — used when generating future occurrences."""
        if self.status == self.Status.LEFT:
            return False
        if self.status == self.Status.LEAVING and self.leaves_on and moment >= self.leaves_on:
            return False
        return True


class ClassSession(models.Model):
    class GroupTier(models.TextChoices):
        GROUP_10 = "GROUP_10", "Group of 10"
        GROUP_8 = "GROUP_8", "Group of 8"
        GROUP_6 = "GROUP_6", "Group of 6"
        GROUP_5 = "GROUP_5", "Group of 5"
        GROUP_4 = "GROUP_4", "Group of 4"
        GROUP_3 = "GROUP_3", "Group of 3"
        GROUP_2 = "GROUP_2", "Group of 2"
        INDIVIDUAL = "INDIVIDUAL", "Individual"

    TIER_CAPACITY = {
        GroupTier.GROUP_10: 10, GroupTier.GROUP_8: 8, GroupTier.GROUP_6: 6, GroupTier.GROUP_5: 5,
        GroupTier.GROUP_4: 4, GroupTier.GROUP_3: 3, GroupTier.GROUP_2: 2, GroupTier.INDIVIDUAL: 1,
    }

    # Default minimum enrollment threshold per tier (~50% of capacity),
    # applied automatically on creation unless explicitly overridden —
    # confirmed with the product owner. INDIVIDUAL has no minimum (always 1).
    DEFAULT_MIN_STUDENTS = {
        GroupTier.GROUP_10: 5, GroupTier.GROUP_8: 4, GroupTier.GROUP_6: 3, GroupTier.GROUP_5: 3,
        GroupTier.GROUP_4: 2, GroupTier.GROUP_3: 2, GroupTier.GROUP_2: 1, GroupTier.INDIVIDUAL: None,
    }
    # How long before the session start the minimum-enrollment deadline falls.
    DEFAULT_MIN_ENROLLMENT_NOTICE = timedelta(hours=24)

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        ASSIGNED = "assigned", "Teacher assigned"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="sessions")
    level = models.CharField(max_length=10, choices=Subject.Level.choices)
    group_tier = models.CharField(max_length=12, choices=GroupTier.choices)
    max_capacity = models.PositiveSmallIntegerField()
    # Spec 5.1: below this threshold by min_enrollment_deadline, the session
    # auto-cancels with a refund. Nullable = no minimum enforced.
    min_students = models.PositiveSmallIntegerField(null=True, blank=True)
    min_enrollment_deadline = models.DateTimeField(null=True, blank=True)

    assigned_teacher = models.ForeignKey(
        TeacherProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_sessions"
    )
    # Optional — set on an INDIVIDUAL session when the student clicked
    # "Réserver un cours avec <nom>" from that teacher's public profile
    # page (see TeacherDetail.jsx / IndividualBookingSerializer). Same
    # spirit as GroupRequest.preferred_teacher: purely informational for
    # whoever assigns assigned_teacher above, never auto-assigned.
    preferred_teacher = models.ForeignKey(
        TeacherProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="preferred_by_sessions"
    )
    series = models.ForeignKey(ClassSeries, on_delete=models.CASCADE, null=True, blank=True, related_name="occurrences")
    # Set when this session was created via the autonomous scheduling flow
    # (GroupAssignmentViewSet.schedule / ClassSessionViewSet.add_extra_session)
    # — direct link back to the package, even for sessions whose series
    # itself already links back (see ClassSeries.group_assignment); makes
    # "all sessions for this package" a single filter either way.
    group_assignment = models.ForeignKey(
        "GroupAssignment", on_delete=models.SET_NULL, null=True, blank=True, related_name="class_sessions"
    )

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)

    # Populated once the video provider room is created (spec: 10 min before start).
    meeting_url = models.URLField(blank=True)
    # Set when meeting_url was created via the Google Calendar API (Google Meet
    # integration) — lets us look the event back up to fetch the recording
    # afterwards. Blank when the Daily.co/Jitsi fallback was used instead.
    calendar_event_id = models.CharField(max_length=255, blank=True)
    # Populated after the fact by `manage.py fetch_meet_recordings`, once
    # Google has finished processing the Meet recording (Drive link).
    recording_url = models.URLField(blank=True)

    class Meta:
        ordering = ["start_time"]

    def save(self, *args, **kwargs):
        """
        Applies the default minimum-enrollment threshold and deadline on
        creation, unless they were explicitly set — works whether the
        session is created via the admin or the API (spec 5.1).
        """
        is_new = self._state.adding
        if is_new:
            if self.min_students is None:
                self.min_students = self.DEFAULT_MIN_STUDENTS.get(self.group_tier)
            if self.min_enrollment_deadline is None and self.min_students is not None and self.start_time:
                self.min_enrollment_deadline = self.start_time - self.DEFAULT_MIN_ENROLLMENT_NOTICE
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.subject} - {self.get_group_tier_display()} @ {self.start_time:%Y-%m-%d %H:%M}"

    @property
    def confirmed_seats_taken(self):
        return self.enrollments.filter(payment_status=Enrollment.PaymentStatus.PAID, waitlisted=False).count()

    @property
    def has_capacity(self):
        return self.confirmed_seats_taken < self.max_capacity


class Material(models.Model):
    """
    Content a teacher shares with their students — a document (PDF, etc.)
    or a link to a video — attached to EITHER:
    - the whole recurring group (`group_assignment`) — visible to every
      student in the group regardless of which weekly slot they attend,
      the normal case for anything created via the autonomous scheduling
      model; or
    - a single one-off session (`class_session`) — for INDIVIDUAL
      bookings, and any session with no GroupAssignment at all (e.g. the
      legacy AdminScheduleGroupView path).
    Exactly one of the two must be set — enforced in MaterialSerializer.validate,
    not at the DB level (Django has no clean XOR constraint).
    """
    class ContentType(models.TextChoices):
        DOCUMENT = "document", "Document (PDF, etc.)"
        VIDEO_LINK = "video_link", "Lien vidéo"

    group_assignment = models.ForeignKey(
        GroupAssignment, on_delete=models.CASCADE, null=True, blank=True, related_name="materials"
    )
    class_session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, null=True, blank=True, related_name="materials")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="uploaded_materials")
    content_type = models.CharField(max_length=12, choices=ContentType.choices, default=ContentType.DOCUMENT)
    title = models.CharField(max_length=200)
    # Exactly one of file/url is set, matching content_type — see
    # MaterialSerializer.validate.
    file = models.FileField(upload_to="materials/%Y/%m/", blank=True)
    url = models.URLField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Enrollments / bookings
# ---------------------------------------------------------------------------
class Enrollment(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        REFUNDED = "refunded", "Refunded"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments")
    class_session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="enrollments")
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    # Spec 5.1: true while the student is on the waitlist for a full group.
    waitlisted = models.BooleanField(default=False)
    booked_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = ("student", "class_session")
        ordering = ["-booked_at"]

    def __str__(self):
        return f"{self.student} -> {self.class_session} ({self.payment_status})"


# ---------------------------------------------------------------------------
# Payments & subscriptions (Stripe-backed)
# ---------------------------------------------------------------------------
class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"

    class Gateway(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        # Konnect (spec: paiement en Tunisie — Stripe ne prend pas en charge
        # le dinar tunisien, devise à change restreint. Voir
        # core/services/konnect.py). Contrairement à Stripe, Konnect ne
        # gère aucun prélèvement récurrent automatique : chaque paiement
        # Konnect est ponctuel, y compris pour un forfait groupe mensuel
        # (l'élève doit relancer un paiement chaque mois — voir
        # SeriesMembershipViewSet.checkout).
        KONNECT = "konnect", "Konnect"
        # Virement bancaire manuel (Tunisie) : pas de compte marchand
        # Konnect confirmé, donc pas de paiement automatisé pour l'instant.
        # L'élève contacte l'admin par e-mail, fait le virement sur le
        # compte bancaire tunisien de l'admin, qui approuve ensuite
        # manuellement dans l'admin Django (EnrollmentAdmin /
        # SeriesMembershipAdmin — action "Marquer comme payé"). Aucune
        # référence de transaction automatique : le champ
        # konnect_payment_ref reste vide pour ce gateway.
        BANK_TRANSFER = "bank_transfer", "Virement bancaire (manuel)"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments")
    enrollment = models.ForeignKey(Enrollment, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    amount = models.DecimalField(max_digits=8, decimal_places=2, validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="EUR")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    gateway = models.CharField(max_length=15, choices=Gateway.choices, default=Gateway.STRIPE)
    # Captured when we redirect the user to Checkout — used to match the
    # `checkout.session.completed` webhook event back to this row.
    stripe_checkout_session_id = models.CharField(max_length=255, blank=True)
    # The underlying PaymentIntent id (pi_...), captured from the webhook
    # event once payment is confirmed — THIS is what a refund actually
    # needs (`stripe.Refund.create(payment_intent=...)`), not the checkout
    # session id above. Blank until the webhook fires.
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True)
    # Konnect's own payment reference (`paymentRef` from init-payment) —
    # equivalent role to stripe_checkout_session_id above, used to match
    # the Konnect webhook callback back to this row.
    konnect_payment_ref = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment {self.amount}{self.currency} - {self.status}"


class ReferralCommission(models.Model):
    """
    Programme de parrainage (10% — voir core/services/referrals.py) : une
    ligne par paiement effectif d'un élève parrainé, pas par élève ni par
    groupe entier — si un enseignant/affilié n'a parrainé que 2 élèves
    d'un groupe de 10, seuls les paiements de ces 2 élèves génèrent une
    commission. `payment` étant OneToOne, un même paiement Stripe ne peut
    jamais générer deux commissions (protège contre les retries de
    webhook). Ouvert à tout type de compte (élève, enseignant, ou pur
    affilié via User.Role.AFFILIATE) — `referrer` est un User générique,
    pas limité à TeacherProfile.
    """
    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_commissions_earned"
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_commissions_generated"
    )
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE, related_name="referral_commission")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    # Copiée de payment.currency à la création — JAMAIS convertie ni
    # additionnée avec une commission d'une autre devise (un filleul payé
    # en EUR via Stripe et un autre en TND via Konnect génèrent deux
    # commissions dans deux devises distinctes, non fongibles entre elles
    # — voir core/services/referrals.py et AdminReferralsView).
    currency = models.CharField(max_length=3, default="EUR")
    rate = models.DecimalField(max_digits=4, decimal_places=2)
    # Pas de virement automatisé (même logique que les heures enseignants,
    # voir AdminTeacherHoursView) — l'admin coche une fois payé, à la main.
    paid_out = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.referrer} <- {self.student} : {self.amount} {self.currency}"


class PricingRate(models.Model):
    """
    Per-hour rate for one group tier — the editable source of truth for
    pricing, changeable from the Django admin without a code deploy.
    `core/pricing.py` reads from this table (falling back to a hardcoded
    default for any tier that has no row yet, so the system works out of
    the box even before an admin has touched this) — see that module for
    how it's actually used in price calculations.
    Seed the initial rows with: `python manage.py seed_pricing`.

    Two independent prices per tier, not a currency conversion of one
    into the other: EUR is Stripe's price (rest of world), TND is
    Konnect's price (Tunisia only — see core/services/konnect.py). Set
    directly per market, since a straight EUR->TND exchange-rate
    conversion would produce prices that don't match local expectations.
    """
    GROUP_TIER_CHOICES = [
        ("GROUP_10", "Groupe de 10"), ("GROUP_8", "Groupe de 8"), ("GROUP_6", "Groupe de 6"), ("GROUP_5", "Groupe de 5"),
        ("GROUP_4", "Groupe de 4"), ("GROUP_3", "Groupe de 3"), ("GROUP_2", "Groupe de 2"), ("INDIVIDUAL", "Individuel"),
    ]

    group_tier = models.CharField(max_length=12, choices=GROUP_TIER_CHOICES, unique=True)
    price_per_hour_cents = models.PositiveIntegerField(help_text="Prix en centimes d'euro, par heure de cours.")
    # 1 dinar tunisien = 1000 millimes (comme les centimes pour l'euro) —
    # unité aussi attendue telle quelle par l'API Konnect (voir
    # core/services/konnect.py). Prix propre au marché tunisien, PAS une
    # conversion automatique du prix EUR ci-dessus.
    price_per_hour_millimes_tnd = models.PositiveIntegerField(
        default=0, help_text="Prix en millimes de dinar tunisien (1 DT = 1000 millimes), par heure de cours — marché tunisien (Konnect)."
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_group_tier_display()} — {self.price_per_hour_cents / 100:.2f}€/h"


class Subscription(models.Model):
    """
    Abonnement d'un élève à UN SelfStudyPlan précis — voir son docstring.
    Une ligne par (student, plan), PAS par student seul : un élève abonné
    à "Terminale Spé" ET "Maths Expertes" a deux Subscription distinctes,
    facturées séparément sur Stripe (deux stripe_subscription_id
    différents), résiliables indépendamment l'une de l'autre.
    """
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="selfstudy_subscriptions")
    plan = models.ForeignKey(SelfStudyPlan, on_delete=models.CASCADE, related_name="subscriptions")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    current_period_end = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "plan"], name="unique_subscription_per_user_plan")
        ]

    def __str__(self):
        return f"{self.user} — {self.plan} ({self.status})"


class Payout(models.Model):
    """
    Teacher compensation for a payout period — spec 5.4.

    Une ligne par (teacher, period, currency) — PAS une seule ligne par
    (teacher, period). Un enseignant qui a eu des élèves payant en EUR
    (Stripe) ET des élèves tunisiens payant en TND (Konnect) sur la même
    période reçoit DEUX Payout distincts, un par devise, réglés
    séparément (virement bancaire européen vs Konnect/virement tunisien).
    Ne jamais additionner Payout.amount entre lignes de devises
    différentes — voir core/management/commands/compute_payouts.py.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"

    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name="payouts")
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    currency = models.CharField(max_length=3, default="EUR")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "period_start", "period_end", "currency"],
                name="unique_payout_per_teacher_period_currency",
            )
        ]

    def __str__(self):
        return f"Payout {self.teacher} {self.period_start}-{self.period_end} {self.currency} ({self.status})"


# ---------------------------------------------------------------------------
# Forum
# ---------------------------------------------------------------------------
class ForumThread(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forum_threads")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="forum_threads")
    level = models.CharField(max_length=10, choices=Subject.Level.choices)
    title = models.CharField(max_length=200)
    body = models.TextField()
    is_solved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ForumReply(models.Model):
    thread = models.ForeignKey(ForumThread, on_delete=models.CASCADE, related_name="replies")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="forum_replies")
    body = models.TextField()
    is_accepted_answer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Reply by {self.user} on {self.thread}"


# ---------------------------------------------------------------------------
# Landing page content (public marketing site) — FAQ and static legal pages.
# ---------------------------------------------------------------------------
class FAQ(models.Model):
    """A question/answer pair shown in the landing page's FAQ accordion."""
    question = models.CharField(max_length=300)
    response = models.TextField()
    order = models.PositiveIntegerField(default=0, help_text="Ordre d'affichage (croissant).")
    is_visible = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQ"

    def __str__(self):
        return self.question


class StaticPage(models.Model):
    """
    A simple CMS entry for a legal/static page (mentions légales, CGV,
    politique de confidentialité...) — editable from the Django admin, no
    code deploy needed to update legal text. `content` is rendered as
    plain paragraphs on the frontend (one per blank-line-separated block)
    — keep it simple text, no HTML needed.
    """
    class Slug(models.TextChoices):
        MENTIONS_LEGALES = "mentions-legales", "Mentions légales"
        CGV = "cgv", "Conditions générales de vente"
        CONFIDENTIALITE = "confidentialite", "Politique de confidentialité"

    slug = models.CharField(max_length=30, choices=Slug.choices, unique=True)
    title = models.CharField(max_length=200)
    content = models.TextField(help_text="Texte brut — un paragraphe par ligne vide.")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class NewsletterSubscriber(models.Model):
    """
    An email address collected from the landing page's footer newsletter
    form (see PublicNewsletterSubscribeView). Kept locally as the source
    of truth even though the contact is also pushed to Brevo — so the
    signup still succeeds (and isn't lost) if Brevo is briefly
    unreachable, and so this list survives independently of the Brevo
    account. See core/services/brevo.py for the actual sync.
    """
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    # Set once the contact has been successfully pushed to Brevo — lets a
    # management command retry only the ones that failed, instead of
    # re-pushing everyone every time (see sync_brevo_newsletter command).
    synced_to_brevo = models.BooleanField(default=False)

    class Meta:
        ordering = ["-subscribed_at"]

    def __str__(self):
        return self.email
