"""
Calcul des rabais — combine le rabais global (voir GlobalDiscount, réglé
depuis /admin) et un code promo optionnel (voir PromoCode) sur un
montant en centimes. Utilisé par tous les points de paiement instantané
(réservation individuelle, abonnement contenu autonome) et par
l'affichage public des tarifs de groupe (PublicPricingView).

Les deux rabais se CUMULENT multiplicativement s'ils sont présents tous
les deux (ex: 10% global + 20% promo = prix final × 0.9 × 0.8 = ×0.72,
soit -28%, pas -30%) — choix volontaire pour rester simple, documenté
ici pour que ce ne soit pas une surprise si les deux sont actifs en même
temps.
"""
from django.db.models import F
from django.utils import timezone

from .models import GlobalDiscount, PromoCode


class InvalidPromoCode(Exception):
    """Levée avec un message déjà adapté à l'affichage direct à l'élève."""


def get_global_discount_percent():
    """Renvoie le pourcentage de rabais global actif, ou 0 si aucun n'est actif."""
    discount = GlobalDiscount.objects.first()
    if discount and discount.is_active and discount.percentage:
        return discount.percentage
    return 0


def get_valid_promo_code(code):
    """
    Renvoie l'objet PromoCode si `code` est valide (existe, actif, pas
    expiré, pas épuisé). Renvoie None si `code` est vide/None — un code
    promo est toujours facultatif. Lève InvalidPromoCode sinon.
    """
    if not code:
        return None
    try:
        promo = PromoCode.objects.get(code__iexact=code.strip())
    except PromoCode.DoesNotExist:
        raise InvalidPromoCode("Ce code promo n'existe pas.")
    if not promo.is_active:
        raise InvalidPromoCode("Ce code promo n'est plus actif.")
    if promo.expires_at and timezone.now() > promo.expires_at:
        raise InvalidPromoCode("Ce code promo a expiré.")
    if promo.max_uses is not None and promo.times_used >= promo.max_uses:
        raise InvalidPromoCode("Ce code promo a atteint son nombre maximal d'utilisations.")
    return promo


def apply_discounts(cents, promo_code_str=None):
    """
    Applique le rabais global (s'il est actif) et le code promo (s'il
    est fourni et valide) à un montant en centimes. Renvoie
    (montant_final_cents, promo_ou_None). Lève InvalidPromoCode si un
    code a été fourni mais n'est pas valide — à l'appelant de renvoyer
    ça clairement à l'élève plutôt que d'ignorer silencieusement le code
    ou de n'appliquer que le rabais global.
    """
    promo = get_valid_promo_code(promo_code_str)
    factor = 1.0
    global_pct = get_global_discount_percent()
    if global_pct:
        factor *= (1 - global_pct / 100)
    if promo:
        factor *= (1 - promo.percentage / 100)
    final_cents = round(cents * factor)
    return final_cents, promo


def record_promo_code_use(promo):
    """
    À appeler UNE SEULE FOIS, après la création réussie d'une session de
    paiement Stripe (pas juste après validation du code) — sinon un
    élève qui retape/retente plusieurs fois consommerait le quota sans
    jamais payer.
    """
    if promo is None:
        return
    PromoCode.objects.filter(pk=promo.pk).update(times_used=F("times_used") + 1)
