"""
Intégration Konnect — passerelle de paiement tunisienne, utilisée à la
place de Stripe pour les élèves ayant choisi la Tunisie comme pays
(Stripe ne prend pas en charge le dinar tunisien — devise à change
restreint, non convertible librement hors de Tunisie).

Documentation API : https://docs.konnect.network/docs/fr/api-integration

IMPORTANT — limite connue : contrairement à Stripe, Konnect (et plus
largement aucune passerelle tunisienne à ce jour) ne gère de prélèvement
récurrent automatique. Chaque paiement Konnect est un paiement ponctuel
("immediate") — pour un abonnement mensuel de groupe, l'élève doit donc
relancer lui-même un paiement chaque mois (voir
SeriesMembershipViewSet.checkout côté views.py), il n'y a pas de
renouvellement automatique côté Konnect comme il y en a avec les
abonnements Stripe.
"""
import requests
from django.conf import settings


def _base_url():
    return "https://api.sandbox.konnect.network/api/v2" if settings.KONNECT_SANDBOX else "https://api.konnect.network/api/v2"


def _headers():
    return {"x-api-key": settings.KONNECT_API_KEY, "Content-Type": "application/json"}


def init_payment(amount_millimes, description, order_id, webhook_url, user):
    """
    Démarre un paiement Konnect et renvoie l'URL vers laquelle rediriger
    l'élève, ainsi que la référence de paiement à conserver (équivalent du
    stripe_checkout_session_id côté Stripe — voir Payment.konnect_payment_ref).

    `amount_millimes` est déjà en millimes tunisiens (1 DT = 1000
    millimes) — voir core/pricing.py (rate_per_hour_tnd et consorts) pour
    le calcul, propre au marché tunisien, PAS une conversion depuis l'euro.

    `order_id` doit encoder ce que le paiement concerne (ex:
    "enrollment:42" ou "series:17") — Konnect nous le renvoie tel quel
    dans les détails du paiement, c'est ce qui permet au webhook de savoir
    quoi confirmer côté KLASSX (voir KonnectWebhookView).
    """
    payload = {
        "receiverWalletId": settings.KONNECT_WALLET_ID,
        "token": "TND",
        "amount": amount_millimes,
        "type": "immediate",
        "description": description,
        "acceptedPaymentMethods": ["wallet", "bank_card", "e-DINAR"],
        "lifespan": 30,  # minutes avant expiration du lien de paiement
        "checkoutForm": True,
        "orderId": order_id,
        "webhook": webhook_url,
        "firstName": user.first_name or "",
        "lastName": user.last_name or "",
        "email": user.email or "",
    }
    response = requests.post(f"{_base_url()}/payments/init-payment", json=payload, headers=_headers(), timeout=15)
    response.raise_for_status()
    return response.json()  # {"payUrl": "...", "paymentRef": "..."}


def get_payment(payment_ref):
    """Récupère le statut détaillé d'un paiement Konnect par sa référence."""
    response = requests.get(f"{_base_url()}/payments/{payment_ref}", headers=_headers(), timeout=15)
    response.raise_for_status()
    return response.json()["payment"]
