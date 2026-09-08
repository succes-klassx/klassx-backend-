from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.
    Simple ajout de champ, vérifié à la main contre
    models.TeacherProfile.years_of_experience.
    """

    dependencies = [
        ("core", "0018_selfstudy_teacher_submissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacherprofile",
            name="years_of_experience",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
