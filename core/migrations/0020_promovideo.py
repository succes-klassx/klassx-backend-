from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.
    CreateModel simple, vérifiée à la main contre models.PromoVideo.
    """

    dependencies = [
        ("core", "0019_teacher_years_of_experience"),
    ]

    operations = [
        migrations.CreateModel(
            name="PromoVideo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("video_url", models.URLField()),
                ("is_active", models.BooleanField(default=True)),
                ("order_index", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["order_index", "-created_at"],
            },
        ),
    ]
