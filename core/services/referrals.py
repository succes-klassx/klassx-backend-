"""
Programme de parrainage — 10% du montant de chaque paiement d'un élève
parrainé, versés au parrain (élève, enseignant, ou affilié pur).

Appelé à chaque paiement confirmé (voir core/views.py :
mark_series_enrollments_paid et StripeWebhookView._confirm_enrollment_payment)
— aussi bien le premier mois que chaque mois suivant pour un abonnement
récurrent, donc la commission suit naturellement les paiements réels : si
l'élève parrainé arrête de payer, aucune nouvelle commission n'est générée
pour lui à partir de ce moment-là. Calculé par élève individuellement, pas
par groupe entier (spec confirmée avec le product owner).

La commission est dans la même devise que le paiement qui l'a générée
(EUR via Stripe, TND via Konnect pour les élèves en Tunisie — voir
core/services/konnect.py) — jamais convertie. Ne JAMAIS additionner des
ReferralCommission de devises différentes ensemble : un total affiché
doit toujours être groupé par currency (voir AdminReferralsView et
UserSerializer.get_referral_earnings_total).
"""
from decimal import Decimal

REFERRAL_COMMISSION_RATE = Decimal("0.10")  # 10%


def create_referral_commission_if_applicable(payment):
    """
    Si payment.user a été parrainé (User.referred_by renseigné à
    l'inscription), crée la ligne ReferralCommission correspondante.
    Ne fait rien si l'élève n'a pas de parrain, ou si une commission
    existe déjà pour ce paiement précis (idempotent — protège contre les
    retries de webhook Stripe, qui peuvent renvoyer le même événement
    plusieurs fois).
    """
    from core.models import ReferralCommission

    referrer = payment.user.referred_by
    if referrer is None:
        return None

    amount = (payment.amount * REFERRAL_COMMISSION_RATE).quantize(Decimal("0.01"))
    commission, _created = ReferralCommission.objects.get_or_create(
        payment=payment,
        defaults={
            "referrer": referrer, "student": payment.user, "amount": amount,
            "currency": payment.currency, "rate": REFERRAL_COMMISSION_RATE,
        },
    )
    return commission
