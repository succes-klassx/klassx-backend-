"""
Crée les 6 abonnements de contenu en libre-service (spec : vidéos + PDF,
sans accompagnement enseignant) — voir SelfStudyPlan.

Sans danger à relancer (get_or_create sur `code`) — n'écrase jamais un
prix déjà modifié depuis l'admin.
"""
from django.core.management.base import BaseCommand

from core.models import SelfStudyPlan

PLANS = [
    (SelfStudyPlan.MathTrack.PREMIERE_NON_SPE, "Mathématiques — 1ère (tronc commun)"),
    (SelfStudyPlan.MathTrack.PREMIERE_SPE, "Mathématiques — 1ère Spécialité"),
    (SelfStudyPlan.MathTrack.PREMIERE_TECHNO, "Mathématiques — 1ère Technologique"),
    (SelfStudyPlan.MathTrack.TERMINALE_SPE, "Mathématiques — Terminale Spécialité"),
    (SelfStudyPlan.MathTrack.TERMINALE_MATHS_EXPERTES, "Mathématiques — Terminale Expertes"),
    (SelfStudyPlan.MathTrack.TERMINALE_MATHS_COMPLEMENTAIRES, "Mathématiques — Terminale Complémentaires"),
]


class Command(BaseCommand):
    help = "Crée les 6 plans d'abonnement de contenu Maths en libre-service (4,99€/mois chacun) s'ils n'existent pas déjà."

    def handle(self, *args, **options):
        created = 0
        for code, name in PLANS:
            _, was_created = SelfStudyPlan.objects.get_or_create(code=code, defaults={"name": name, "price_cents": 499})
            created += was_created
        self.stdout.write(self.style.SUCCESS(f"{created} plan(s) créé(s) ({len(PLANS)} au total)."))
