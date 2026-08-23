"""
Intégration Brevo (ex-Sendinblue) — utilisée pour ajouter les inscrits à
la newsletter du footer à une liste de contacts Brevo, pour ensuite
gérer les campagnes email depuis l'interface Brevo directement.

Documentation API : https://developers.brevo.com/reference/createcontact

Best-effort volontairement : si Brevo est indisponible ou mal configuré,
l'inscription reste enregistrée localement (voir NewsletterSubscriber) —
seul le push vers Brevo échoue, et peut être rejoué plus tard via
`python manage.py sync_brevo_newsletter`. Un souci Brevo ne doit jamais
empêcher un visiteur de s'inscrire depuis la page d'accueil.
"""
import requests
from django.conf import settings

BASE_URL = "https://api.brevo.com/v3"


class BrevoError(Exception):
    """Levée si l'appel à l'API Brevo échoue — capturée par l'appelant, ne remonte jamais jusqu'à l'utilisateur."""


def _headers():
    return {
        "api-key": settings.BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def is_configured():
    return bool(settings.BREVO_API_KEY)


def add_contact(email):
    """
    Ajoute (ou met à jour, si l'email existe déjà) un contact Brevo, et
    l'inscrit à la liste BREVO_NEWSLETTER_LIST_ID si celle-ci est définie.
    Lève BrevoError en cas d'échec — à l'appelant de décider quoi en
    faire (voir PublicNewsletterSubscribeView : on logue et on continue).
    """
    if not is_configured():
        raise BrevoError("BREVO_API_KEY n'est pas configurée.")

    payload = {"email": email, "updateEnabled": True}
    if settings.BREVO_NEWSLETTER_LIST_ID:
        payload["listIds"] = [int(settings.BREVO_NEWSLETTER_LIST_ID)]

    try:
        response = requests.post(f"{BASE_URL}/contacts", json=payload, headers=_headers(), timeout=10)
        # Brevo renvoie 204 si le contact existait déjà et a juste été
        # mis à jour (updateEnabled=True) — ce n'est pas une erreur.
        if response.status_code not in (201, 204):
            raise BrevoError(f"Brevo a répondu {response.status_code}: {response.text}")
    except requests.RequestException as exc:
        raise BrevoError(f"Appel à l'API Brevo impossible: {exc}") from exc
