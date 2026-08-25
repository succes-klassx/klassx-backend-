from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils import timezone

import logging

from .models import (
    ClassSeries, ClassSession, Enrollment, FAQ, ForumReply, ForumThread,
    GroupAnnouncement, GroupAssignment, GroupRequest, Material, NewsletterSubscriber, ParentalConsent, Payment,
    Payout, PricingRate, ReferralCommission, SeriesMembership, StaticPage,
    SelfStudyContentItem, SelfStudyPlan, StudentProfile, Subject, Subscription, TeacherAvailability, TeacherProfile,
    TeacherSubject, User, VideoProgress,
)
from .services import video

logger = logging.getLogger(__name__)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ["username", "email", "role", "first_name", "last_name", "is_active"]
    list_filter = ["role", "is_active"]
    fieldsets = DjangoUserAdmin.fieldsets + (("KLASSX", {"fields": ("role", "phone", "country")}),)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user", "bac_type", "grade_level", "cecrl_level", "candidate_type", "terminale_math_option",
        "date_of_birth", "is_minor", "timezone",
    ]
    list_filter = ["bac_type", "grade_level", "cecrl_level", "candidate_type"]
    filter_horizontal = ["premiere_specialties", "terminale_specialties"]

    @admin.display(boolean=True, description="Mineur")
    def is_minor(self, obj):
        return obj.is_minor


@admin.register(ParentalConsent)
class ParentalConsentAdmin(admin.ModelAdmin):
    """
    See ParentalConsent's docstring (core/models.py) — le consentement est
    désormais confirmé immédiatement à l'inscription (mot de passe choisi
    conjointement par le parent et l'élève), donc ceci n'a normalement
    jamais besoin d'être touché. L'action "Marquer comme confirmé" reste
    une porte de secours pour d'éventuels cas historiques ou migrés
    manuellement en base à l'état PENDING.
    """
    list_display = ["student", "parent_full_name", "parent_email", "status", "requested_at", "confirmed_at"]
    list_filter = ["status"]
    search_fields = ["student__user__email", "parent_email", "parent_full_name"]
    readonly_fields = ["requested_at", "confirmed_ip"]
    actions = ["mark_as_confirmed"]

    @admin.action(description="Marquer comme confirmé")
    def mark_as_confirmed(self, request, queryset):
        updated = queryset.exclude(status=ParentalConsent.Status.CONFIRMED).update(
            status=ParentalConsent.Status.CONFIRMED, confirmed_at=timezone.now()
        )
        self.message_user(request, f"{updated} autorisation(s) marquée(s) comme confirmée(s).")


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "is_active", "is_featured", "subject", "compensation_type", "compensation_rate", "default_meeting_url", "google_connected"]
    list_filter = ["is_active", "is_featured"]
    list_editable = ["is_featured"]
    readonly_fields = ["google_oauth_refresh_token"]  # set only via the connect flow, never hand-edited


admin.site.register(TeacherAvailability)
admin.site.register(TeacherSubject)
@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "bac_type", "cecrl_level", "level", "subject_type", "hours_per_week_premiere", "hours_per_week_terminale"]
    list_filter = ["bac_type", "subject_type", "level", "cecrl_level"]


@admin.register(SelfStudyPlan)
class SelfStudyPlanAdmin(admin.ModelAdmin):
    """
    Les 5 abonnements — voir `python manage.py seed_selfstudy_plans` pour
    les créer la première fois. Le prix (price_cents) est modifiable ici
    directement, sans déploiement de code ni configuration Stripe
    séparée (voir core/services/payments.py:create_subscription_checkout_session).
    """
    list_display = ["name", "code", "price_cents", "price_eur", "is_active"]
    list_editable = ["price_cents", "is_active"]

    def price_eur(self, obj):
        return f"{obj.price_cents / 100:.2f}€/mois"


@admin.register(SelfStudyContentItem)
class SelfStudyContentItemAdmin(admin.ModelAdmin):
    """
    C'est ICI que vous débloquez le contenu du mois pour les abonnés
    (spec). Un item créé ici reste invisible côté élève tant que
    `is_unlocked` n'est pas coché — préparez tout le contenu du mois à
    l'avance sans risque, puis cochez (directement dans la liste via
    `is_unlocked` ci-dessous, ou en masse via l'action "Débloquer le
    contenu sélectionné") quand vous êtes prêt à le rendre visible aux
    abonnés actifs de ce plan. Seuls les abonnés dont le paiement est à
    jour (Subscription.status=ACTIVE) le voient — voir
    SelfStudyContentViewSet.
    """
    list_display = ["title", "plan", "content_type", "month", "chapter_name", "is_unlocked", "order_index"]
    list_filter = ["plan", "content_type", "month", "is_unlocked"]
    list_editable = ["is_unlocked", "order_index"]
    search_fields = ["title", "chapter_name"]
    date_hierarchy = "month"
    actions = ["unlock_selected", "lock_selected"]

    @admin.action(description="Débloquer le contenu sélectionné")
    def unlock_selected(self, request, queryset):
        updated = queryset.update(is_unlocked=True)
        self.message_user(request, f"{updated} élément(s) débloqué(s).")

    @admin.action(description="Reverrouiller le contenu sélectionné")
    def lock_selected(self, request, queryset):
        updated = queryset.update(is_unlocked=False)
        self.message_user(request, f"{updated} élément(s) reverrouillé(s).")


@admin.register(Subscription)
class SelfStudySubscriptionAdmin(admin.ModelAdmin):
    """Un élève peut apparaître plusieurs fois ici — une ligne par plan auquel il est abonné, voir Subscription.Meta."""
    list_display = ["user", "plan", "status", "current_period_end", "created_at"]
    list_filter = ["status", "plan"]
    search_fields = ["user__email", "user__first_name", "user__last_name"]
    actions = ["mark_paid_bank_transfer"]

    @admin.action(description="Activer/renouveler pour 30 jours (virement bancaire Tunisie reçu)")
    def mark_paid_bank_transfer(self, request, queryset):
        """
        Équivalent de EnrollmentAdmin.mark_paid_bank_transfer pour un
        abonnement capsules vidéo. Comme pour les cours, un virement ne
        couvre qu'un mois (30 jours) à la fois, pas de reconduction
        automatique — l'élève (ou l'admin pour lui) doit relancer un
        virement le mois suivant, comme pour Stripe/Konnect ceci dit sans
        webhook pour le rappeler automatiquement.
        """
        from datetime import timedelta

        from django.utils import timezone as django_timezone

        from .models import Payment

        done = 0
        for subscription in queryset.select_related("user", "plan"):
            if subscription.user.country != "Tunisie":
                continue
            subscription.status = Subscription.Status.ACTIVE
            subscription.current_period_end = django_timezone.now() + timedelta(days=30)
            subscription.save(update_fields=["status", "current_period_end"])
            Payment.objects.create(
                user=subscription.user, amount=subscription.plan.price_millimes_tnd / 1000,
                currency="TND", status=Payment.Status.SUCCEEDED, gateway=Payment.Gateway.BANK_TRANSFER,
            )
            done += 1
        self.message_user(request, f"{done} abonnement(s) activé(s)/renouvelé(s) pour 30 jours.")


admin.site.register(VideoProgress)
admin.site.register(ClassSeries)


@admin.register(SeriesMembership)
class SeriesMembershipAdmin(admin.ModelAdmin):
    list_display = ["student", "series", "status", "monthly_price_cents", "joined_at", "leaves_on"]
    list_filter = ["status"]
    actions = ["mark_paid_bank_transfer"]

    @admin.action(description="Marquer le mois comme payé (virement bancaire Tunisie reçu)")
    def mark_paid_bank_transfer(self, request, queryset):
        """
        Équivalent de EnrollmentAdmin.mark_paid_bank_transfer pour un
        abonnement groupe : réactive l'abonnement et marque payées toutes
        les occurrences en attente du mois en cours (voir
        mark_series_enrollments_paid, la même fonction utilisée par le
        webhook Konnect). Comme Konnect, un virement ne couvre qu'un mois
        à la fois — l'élève (ou l'admin pour lui) doit relancer un
        virement le mois suivant.
        """
        from .pricing import package_monthly_price_millimes_tnd
        from .views import mark_series_enrollments_paid

        done = 0
        for membership in queryset.select_related("series", "student"):
            if membership.student.country != "Tunisie":
                continue
            membership.status = SeriesMembership.Status.ACTIVE
            membership.save(update_fields=["status"])
            amount_millimes = package_monthly_price_millimes_tnd(
                membership.series.group_tier, membership.series.weekly_hours
            )
            mark_series_enrollments_paid(
                membership, gateway=Payment.Gateway.BANK_TRANSFER, currency="TND",
                amount=amount_millimes / 1000,
            )
            done += 1
        self.message_user(request, f"{done} abonnement(s) marqué(s) comme payé(s) pour ce mois.")


@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = ["subject", "level", "group_tier", "start_time", "status", "assigned_teacher", "preferred_teacher"]
    list_filter = ["status", "group_tier", "level", "subject", "preferred_teacher"]

    def save_model(self, request, obj, form, change):
        """
        Génère aussi un lien de visio pour une session créée/modifiée à la
        main dans l'admin — jusqu'ici seuls les vrais flux applicatifs
        (réservation, assignation, planification de groupe) le
        déclenchaient, ce qui laissait les sessions créées ici sans lien.
        Utilise create_room_for_session_full (pas le simple
        create_room_for_session) pour aussi récupérer le
        calendar_event_id — cohérent avec ces autres flux. Comme pour
        eux, une erreur ne bloque jamais l'enregistrement : elle est
        seulement loggée (visible dans le terminal `manage.py runserver`).
        """
        super().save_model(request, obj, form, change)
        if not obj.meeting_url:
            try:
                obj.meeting_url, event_id = video.create_room_for_session_full(obj)
                update_fields = ["meeting_url"]
                if event_id:
                    obj.calendar_event_id = event_id
                    update_fields.append("calendar_event_id")
                obj.save(update_fields=update_fields)
            except Exception:
                logger.exception("Video room creation failed for session %s (admin save)", obj.id)


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ["student", "class_session", "payment_status", "waitlisted", "booked_at"]
    list_filter = ["payment_status", "waitlisted"]
    actions = ["mark_paid_bank_transfer"]

    @admin.action(description="Marquer comme payé (virement bancaire Tunisie reçu)")
    def mark_paid_bank_transfer(self, request, queryset):
        """
        Approbation manuelle du paiement Tunisie (pas d'intermédiaire type
        Konnect/Flouci) : à utiliser une fois le virement bancaire reçu sur
        le compte de l'admin. Reproduit ce que fait normalement le webhook
        Stripe/Konnect (marque payé + enregistre un Payment + déclenche la
        commission de parrainage éventuelle), mais déclenché à la main.
        Ignore silencieusement les lignes non tunisiennes ou déjà payées,
        pour qu'on puisse sélectionner sans risque toute une page de
        résultats filtrés sur payment_status=pending.
        """
        from django.db import transaction as db_transaction

        from . import notifications
        from .pricing import session_price_millimes_tnd
        from .services.referrals import create_referral_commission_if_applicable

        done = 0
        for enrollment in queryset.select_related("class_session__subject", "student"):
            if enrollment.student.country != "Tunisie":
                continue
            if enrollment.payment_status == Enrollment.PaymentStatus.PAID:
                continue
            with db_transaction.atomic():
                enrollment.payment_status = Enrollment.PaymentStatus.PAID
                enrollment.save(update_fields=["payment_status"])
                amount_millimes = session_price_millimes_tnd(enrollment.class_session)
                payment = Payment.objects.create(
                    user=enrollment.student, enrollment=enrollment,
                    amount=amount_millimes / 1000, currency="TND",
                    status=Payment.Status.SUCCEEDED, gateway=Payment.Gateway.BANK_TRANSFER,
                )
                create_referral_commission_if_applicable(payment)
            notifications.send_enrollment_confirmed(enrollment)
            done += 1
        self.message_user(request, f"{done} inscription(s) marquée(s) comme payée(s).")


@admin.register(GroupRequest)
class GroupRequestAdmin(admin.ModelAdmin):
    """
    The "who's waiting to be grouped" view — sort/filter by subject, level,
    and group size to spot when enough matching requests have piled up to
    form a group (see the AdminAssignGroupView API action, exposed on the
    frontend admin dashboard).
    """
    list_display = ["student", "subject", "level", "group_tier", "weekly_hours", "preferred_teacher", "status", "created_at"]
    list_filter = ["status", "subject", "level", "group_tier", "weekly_hours", "preferred_teacher"]
    ordering = ["subject", "level", "group_tier", "created_at"]


@admin.register(GroupAssignment)
class GroupAssignmentAdmin(admin.ModelAdmin):
    """Groups assigned to a teacher, waiting for (or already given) a schedule — see models.GroupAssignment."""
    list_display = ["subject", "level", "group_tier", "teacher", "status", "created_at"]
    list_filter = ["status", "subject", "level", "group_tier"]


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["title", "content_type", "group_assignment", "class_session", "uploaded_by", "uploaded_at"]
    list_filter = ["content_type"]


@admin.register(GroupAnnouncement)
class GroupAnnouncementAdmin(admin.ModelAdmin):
    list_display = ["group_assignment", "author", "created_at"]
@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["user", "amount", "currency", "gateway", "status", "created_at"]
    list_filter = ["gateway", "status", "currency"]


@admin.register(ReferralCommission)
class ReferralCommissionAdmin(admin.ModelAdmin):
    list_display = ["referrer", "student", "amount", "currency", "rate", "paid_out", "created_at"]
    list_filter = ["paid_out", "currency"]


@admin.register(PricingRate)
class PricingRateAdmin(admin.ModelAdmin):
    """Editable pricing table — change a rate here, no code deploy needed. Run `python manage.py seed_pricing` once to create the initial rows."""
    list_display = [
        "group_tier", "price_per_hour_cents", "price_per_hour_eur",
        "price_per_hour_millimes_tnd", "price_per_hour_tnd", "updated_at",
    ]
    list_editable = ["price_per_hour_cents", "price_per_hour_millimes_tnd"]

    def price_per_hour_eur(self, obj):
        return f"{obj.price_per_hour_cents / 100:.2f}€/h"

    def price_per_hour_tnd(self, obj):
        return f"{obj.price_per_hour_millimes_tnd / 1000:.2f} DT/h"


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """
    Payout rows are generated by `python manage.py compute_payouts` (see
    that command's docstring) — this admin is where you actually mark one
    as paid once you've sent the transfer, via the "Marquer comme payé"
    action below. A PAID payout is left alone by future runs of the
    command, so this action is effectively the final step of the flow.
    """
    list_display = ["teacher", "period_start", "period_end", "amount", "currency", "status", "paid_at"]
    list_filter = ["status", "currency", "period_start"]
    search_fields = ["teacher__user__email", "teacher__user__first_name", "teacher__user__last_name"]
    actions = ["mark_as_paid"]

    @admin.action(description="Marquer comme payé")
    def mark_as_paid(self, request, queryset):
        updated = queryset.exclude(status=Payout.Status.PAID).update(
            status=Payout.Status.PAID, paid_at=timezone.now()
        )
        self.message_user(request, f"{updated} versement(s) marqué(s) comme payé(s).")


admin.site.register(ForumThread)
admin.site.register(ForumReply)


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ["question", "order", "is_visible"]
    list_editable = ["order", "is_visible"]
    ordering = ["order"]


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    """Edit legal page content here — mentions légales, CGV, politique de confidentialité. No code deploy needed."""
    list_display = ["title", "slug", "updated_at"]
    readonly_fields = ["updated_at"]


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    """Inscrits à la newsletter du footer — voir aussi la liste correspondante dans Brevo."""
    list_display = ["email", "subscribed_at", "synced_to_brevo"]
    list_filter = ["synced_to_brevo"]
    search_fields = ["email"]
    readonly_fields = ["subscribed_at"]
    ordering = ["-subscribed_at"]
