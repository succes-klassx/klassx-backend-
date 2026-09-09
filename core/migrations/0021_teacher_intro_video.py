from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.
    Simple ajout de champ, vérifié à la main contre
    models.TeacherProfile.intro_video_url.
    """

    dependencies = [
        ("core", "0020_promovideo"),
    ]

    operations = [
        migrations.AddField(
            model_name="teacherprofile",
            name="intro_video_url",
            field=models.URLField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
