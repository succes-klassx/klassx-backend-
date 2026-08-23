"""
Seeds a starter Subject catalog. Simplified, on request, to exactly the 11
subjects needed to unblock testing right now (Bac Général, Première +
Terminale, plus the 2 Terminale math options) — safe to re-run
(get_or_create keyed on `code`, never overwrites a subject you've already
edited via the admin). Add more subjects (other tracks, other
specialties...) directly from the Django admin whenever you need them —
this command is just a quick starting point, not meant to be a complete
catalog.
"""
from django.core.management.base import BaseCommand

from core.models import BacType, CecrlLevel, Subject


def s(code, name, subject_type, level=Subject.Level.BOTH, hours_1ere=None, hours_term=None,
      bac_type=BacType.GENERAL, cecrl_level=""):
    return {
        "code": code, "name": name, "bac_type": bac_type, "subject_type": subject_type, "level": level,
        "hours_per_week_premiere": hours_1ere, "hours_per_week_terminale": hours_term,
        "cecrl_level": cecrl_level,
    }


COMMON = Subject.SubjectType.COMMON_CORE
SPECIALTY = Subject.SubjectType.SPECIALTY
MATH_OPTION = Subject.SubjectType.MATH_OPTION
PREMIERE = Subject.Level.PREMIERE
TERMINALE = Subject.Level.TERMINALE
BOTH = Subject.Level.BOTH

SUBJECTS = [
    s("gen-francais", "Français", COMMON, PREMIERE, hours_1ere=4),
    s("gen-philosophie", "Philosophie", COMMON, TERMINALE, hours_term=4),
    s("gen-histoire-geo", "Histoire-Géographie", COMMON, BOTH, hours_1ere=3, hours_term=3),
    s("gen-anglais", "Anglais", COMMON, BOTH, hours_1ere=2, hours_term=2),
    s("gen-espagnol", "Espagnol", COMMON, BOTH, hours_1ere=2, hours_term=2),
    s("gen-maths", "Mathématiques", SPECIALTY, BOTH, hours_1ere=4, hours_term=6),
    s("gen-physique-chimie", "Physique-Chimie", SPECIALTY, BOTH, hours_1ere=4, hours_term=6),
    s("gen-svt", "SVT", SPECIALTY, BOTH, hours_1ere=4, hours_term=6),
    s("gen-nsi", "Numérique et Sciences Informatiques (NSI)", SPECIALTY, BOTH, hours_1ere=4, hours_term=6),
    # Options de Terminale, pas des spécialités (voir Subject.SubjectType.
    # MATH_OPTION / StudentProfile.terminale_math_option) — Maths Expertes
    # est réservée aux élèves qui gardent Mathématiques en spécialité,
    # Maths Complémentaires à ceux qui l'abandonnent ; validé côté
    # serializers, pas ici.
    s("gen-maths-expertes", "Mathématiques Expertes", MATH_OPTION, TERMINALE, hours_term=3),
    s("gen-maths-complementaires", "Mathématiques Complémentaires", MATH_OPTION, TERMINALE, hours_term=3),

    # FLE — une matière par niveau CECRL (spec : A1 à C2). subject_type
    # COMMON_CORE : pas de système de spécialités pour ce parcours (voir
    # BacType.FLE / validate_specialty_access, qui désactive déjà le
    # filtrage par spécialités dès que bac_type != GENERAL). level=BOTH
    # : Première/Terminale n'a pas de sens ici, voir cecrl_level à la
    # place, qui porte le vrai niveau.
    *[
        s(f"fle-{lvl.value.lower()}", f"FLE — {lvl.label}", COMMON, BOTH,
          bac_type=BacType.FLE, cecrl_level=lvl.value)
        for lvl in CecrlLevel
    ],
    # FLS — soutien linguistique et intégration académique (spec :
    # pas de niveaux CECRL distincts comme le FLE, une seule offre).
    s("fls-soutien", "FLS — Soutien linguistique et intégration académique", COMMON, BOTH, bac_type=BacType.FLS),
]


class Command(BaseCommand):
    help = "Creates a starter Subject catalog (18 subjects — Bac Général, FLE 6 niveaux, FLS) if it doesn't exist yet. Never overwrites existing subjects."

    def handle(self, *args, **options):
        created = 0
        for data in SUBJECTS:
            _, was_created = Subject.objects.get_or_create(code=data["code"], defaults=data)
            if was_created:
                created += 1

        self.stdout.write(self.style.SUCCESS(f"Done — {created} subject(s) created ({len(SUBJECTS)} in the list)."))
