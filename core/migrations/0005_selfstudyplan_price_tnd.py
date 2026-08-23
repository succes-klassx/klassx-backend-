from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (pas d'environnement Django disponible ici pour
    lancer makemigrations) — voir SelfStudyPlan dans core/models.py.

    Deux choses :
    1. AlterField sur `code` : synchronise l'état des migrations avec le
       6e choix (terminale_maths_complementaires) ajouté au modèle après
       la migration 0004 — sans effet en base (choices n'est qu'une
       validation applicative, pas une contrainte SQL), juste pour éviter
       que `makemigrations` ne réclame cette migration plus tard.
    2. AddField `price_millimes_tnd` : prix Tunisie indépendant (virement
       bancaire manuel — voir SubscriptionCheckoutView), même principe que
       PricingRate.price_per_hour_millimes_tnd pour les cours.
    """

    dependencies = [
        ("core", "0004_selfstudy_plans_and_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="selfstudyplan",
            name="code",
            field=models.CharField(choices=[
                ("premiere_non_spe", "1ère — Tronc commun (non spécialité)"),
                ("premiere_spe", "1ère — Spécialité Mathématiques"),
                ("premiere_techno", "1ère Technologique"),
                ("terminale_spe", "Terminale — Spécialité Mathématiques"),
                ("terminale_maths_expertes", "Terminale — Mathématiques Expertes"),
                ("terminale_maths_complementaires", "Terminale — Mathématiques Complémentaires"),
            ], max_length=30, unique=True),
        ),
        migrations.AddField(
            model_name="selfstudyplan",
            name="price_millimes_tnd",
            field=models.PositiveIntegerField(
                default=7000,
                help_text="Prix mensuel en millimes tunisiens (1 DT = 1000 millimes). Défaut : 7 DT.",
            ),
        ),
    ]
