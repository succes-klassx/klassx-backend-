import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.

    - SelfStudyPlan.code : retire la contrainte "choices" (le champ garde
      exactement les mêmes valeurs stockées, juste la liste fermée en
      moins — AlterField sans changement de type ni de données).
    - SelfStudyPlan.subject / assigned_teacher : nouveaux champs
      optionnels (null=True) — les 6 plans Maths existants les auront
      simplement vides après la migration, aucune donnée perdue.
    - SelfStudyContentItem.submitted_by / status : nouveaux champs, avec
      pour "status" un défaut APPROVED — préserve exactement le
      comportement actuel pour tous les items déjà en base (créés par un
      admin, donc déjà de facto "approuvés").
    """

    dependencies = [
        ("core", "0017_blogpost"),
    ]

    operations = [
        migrations.AlterField(
            model_name="selfstudyplan",
            name="code",
            field=models.CharField(max_length=50, unique=True),
        ),
        migrations.AddField(
            model_name="selfstudyplan",
            name="subject",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to="core.subject",
            ),
        ),
        migrations.AddField(
            model_name="selfstudyplan",
            name="assigned_teacher",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="selfstudy_plans", to="core.teacherprofile",
            ),
        ),
        migrations.AddField(
            model_name="selfstudycontentitem",
            name="submitted_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="selfstudy_submissions", to="core.teacherprofile",
            ),
        ),
        migrations.AddField(
            model_name="selfstudycontentitem",
            name="status",
            field=models.CharField(
                choices=[("approved", "Approuvé"), ("pending", "En attente de validation"), ("rejected", "Refusé")],
                default="approved", max_length=10,
            ),
        ),
    ]
