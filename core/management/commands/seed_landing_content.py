"""
Seeds the FAQ and legal static pages (mentions légales, CGV,
confidentialité) with starter content, so the landing page isn't empty out
of the box. Safe to re-run — never overwrites content already edited via
the admin (uses get_or_create).

⚠️ IMPORTANT: the legal page content below is GENERIC PLACEHOLDER TEXT,
not a compliant legal document. It's a starting structure to fill in and
have reviewed by a lawyer before going live — especially given KLASSX
serves minors and operates internationally (data protection / GDPR,
consumer law for online course sales, parental consent...). Replace every
[bracketed] placeholder, then have the final text reviewed.
"""
from django.core.management.base import BaseCommand

from core.models import FAQ, StaticPage

FAQ_ITEMS = [
    (
        "Comment se déroulent les cours en ligne ?",
        "Les cours ont lieu en visioconférence, en groupes de 10, 5 ou 3 élèves (ou en individuel), avec un "
        "enseignant en direct. Chaque séance dure généralement entre 1h et 2h selon la formule choisie.",
    ),
    (
        "Comment gérez-vous le décalage horaire ?",
        "Les créneaux sont fixés par l'enseignant en tenant compte des fuseaux horaires des élèves du groupe, "
        "afin de rester compatibles avec les zones où KLASSX est présent (Amérique du Nord, Europe, Asie...).",
    ),
    (
        "Les cours sont-ils enregistrés ?",
        "Oui, la plupart des séances en direct sont enregistrées et mises à disposition des élèves inscrits, "
        "pour pouvoir les revoir à tout moment depuis leur tableau de bord.",
    ),
    (
        "De quel matériel ai-je besoin pour suivre les cours ?",
        "Un ordinateur (ou une tablette) avec une connexion internet stable, une caméra et un micro suffisent. "
        "Aucun logiciel spécifique à acheter — tout se passe depuis le navigateur.",
    ),
    (
        "Puis-je changer de formule en cours d'année ?",
        "Oui, vous pouvez ajuster votre forfait d'heures hebdomadaires — le changement prend effet le mois "
        "suivant, votre mois en cours n'est jamais interrompu.",
    ),
]

# NOTE: every [bracketed] placeholder below MUST be filled in with your
# real company details before publishing, and the whole text reviewed by
# a lawyer familiar with French/EU consumer and data protection law.
STATIC_PAGES = [
    {
        "slug": StaticPage.Slug.MENTIONS_LEGALES,
        "title": "Mentions légales",
        "content": (
            "Le site KLASSX est édité par [Raison sociale de la société], [forme juridique], au capital de "
            "[montant] euros, immatriculée au Registre du Commerce et des Sociétés de [ville] sous le numéro "
            "[SIRET], dont le siège social est situé [adresse complète].\n\n"
            "Numéro de TVA intracommunautaire : [numéro].\n\n"
            "Directeur de la publication : [nom, prénom].\n\n"
            "Le site est hébergé par [nom de l'hébergeur], [adresse de l'hébergeur].\n\n"
            "Pour toute question, contactez-nous à l'adresse : [email de contact].\n\n"
            "[⚠️ Ceci est un texte générique à personnaliser et faire valider par un professionnel du droit "
            "avant publication.]"
        ),
    },
    {
        "slug": StaticPage.Slug.CGV,
        "title": "Conditions générales de vente",
        "content": (
            "Les présentes conditions générales de vente régissent les relations contractuelles entre "
            "[Raison sociale de la société] (\"KLASSX\") et toute personne (\"le Client\") souscrivant à l'un "
            "de ses services de préparation au Baccalauréat français.\n\n"
            "Article 1 — Objet\n"
            "KLASSX propose des cours en ligne en groupe ou individuels, ainsi que des capsules vidéo, destinés "
            "à la préparation du Baccalauréat français, pour des élèves scolarisés en France ou à l'étranger.\n\n"
            "Article 2 — Tarifs et paiement\n"
            "Les tarifs en vigueur sont ceux affichés sur le site au moment de la commande. Le paiement "
            "s'effectue en ligne, par carte bancaire, via notre prestataire de paiement sécurisé. Les forfaits "
            "récurrents sont facturés mensuellement avec reconduction automatique.\n\n"
            "Article 3 — Droit de rétractation et annulation\n"
            "[Décrire ici les modalités de rétractation légales (délai de 14 jours pour les services numériques "
            "en UE, exceptions applicables) et la politique d'annulation propre à KLASSX (préavis, remboursement).]\n\n"
            "Article 4 — Résiliation d'un forfait récurrent\n"
            "Le Client peut résilier son abonnement à tout moment ; la résiliation prend effet à la fin du mois "
            "en cours, moyennant un préavis de [durée].\n\n"
            "Article 5 — Responsabilité\n"
            "[Clause de responsabilité à faire rédiger par un professionnel du droit.]\n\n"
            "Article 6 — Droit applicable et litiges\n"
            "[Préciser le droit applicable et la juridiction compétente.]\n\n"
            "[⚠️ Ceci est un texte générique à personnaliser et faire valider par un professionnel du droit "
            "avant publication.]"
        ),
    },
    {
        "slug": StaticPage.Slug.CONFIDENTIALITE,
        "title": "Politique de confidentialité",
        "content": (
            "KLASSX attache une grande importance à la protection des données personnelles de ses utilisateurs, "
            "en particulier lorsqu'il s'agit de mineurs.\n\n"
            "Données collectées\n"
            "Nous collectons les informations que vous nous fournissez lors de l'inscription (nom, email, "
            "niveau scolaire, pays...), ainsi que les données liées à l'utilisation du service (participation "
            "aux cours, progression).\n\n"
            "Finalités du traitement\n"
            "Ces données sont utilisées pour fournir le service (organisation des cours, facturation, support), "
            "et ne sont jamais vendues à des tiers.\n\n"
            "Mineurs et consentement parental\n"
            "[Décrire ici les modalités de recueil du consentement parental pour les élèves mineurs, "
            "conformément au RGPD (article 8) et aux réglementations locales applicables selon les pays "
            "d'origine des élèves.]\n\n"
            "Durée de conservation\n"
            "[Préciser la durée de conservation des données selon leur nature.]\n\n"
            "Vos droits\n"
            "Conformément au RGPD, vous disposez d'un droit d'accès, de rectification, d'effacement et de "
            "portabilité de vos données. Pour exercer ces droits, contactez-nous à [email de contact].\n\n"
            "[⚠️ Ceci est un texte générique à personnaliser et faire valider par un professionnel du droit "
            "(RGPD, protection des mineurs, et réglementations locales des pays où KLASSX opère) avant "
            "publication.]"
        ),
    },
]


class Command(BaseCommand):
    help = "Creates starter FAQ entries and legal static pages if they don't exist yet. Never overwrites existing content."

    def handle(self, *args, **options):
        faq_created = 0
        for order, (question, response) in enumerate(FAQ_ITEMS):
            _, created = FAQ.objects.get_or_create(question=question, defaults={"response": response, "order": order})
            if created:
                faq_created += 1

        page_created = 0
        for page in STATIC_PAGES:
            _, created = StaticPage.objects.get_or_create(
                slug=page["slug"], defaults={"title": page["title"], "content": page["content"]}
            )
            if created:
                page_created += 1

        self.stdout.write(self.style.SUCCESS(f"Done — {faq_created} FAQ item(s) and {page_created} page(s) created."))
        if page_created:
            self.stdout.write(
                self.style.WARNING(
                    "⚠️  The legal pages just created contain GENERIC PLACEHOLDER TEXT. "
                    "Edit them in the Django admin and have them reviewed by a lawyer before going live."
                )
            )
