import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme la 0004) plutôt que générée — pas
    d'environnement Django complet disponible pour lancer makemigrations
    dans ce sandbox précis (venv fourni incomplet). CreateModel simple,
    sans ambiguïté : vérifiée à la main contre models.WhiteboardSnapshot.
    """

    dependencies = [
        ("core", "0014_alter_globaldiscount_id_alter_promocode_id"),
    ]

    operations = [
        migrations.CreateModel(
            name="WhiteboardSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("pages", models.JSONField(blank=True, default=list)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "class_session",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="whiteboard",
                        to="core.classsession",
                    ),
                ),
            ],
        ),
    ]
