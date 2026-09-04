import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme la 0004/0015) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.

    Ajoute le tableau personnel de découverte (sans séance) par-dessus
    WhiteboardSnapshot tel que créé dans 0015 (déjà appliquée en
    production — voir models.WhiteboardSnapshot pour le détail) :
    - `class_session` devient facultatif (était obligatoire)
    - nouveau champ `user`, facultatif aussi
    - contrainte : exactement l'un des deux doit être rempli, jamais les
      deux, jamais aucun
    """

    dependencies = [
        ("core", "0015_whiteboardsnapshot"),
    ]

    operations = [
        migrations.AlterField(
            model_name="whiteboardsnapshot",
            name="class_session",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="whiteboard",
                to="core.classsession",
            ),
        ),
        migrations.AddField(
            model_name="whiteboardsnapshot",
            name="user",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="personal_whiteboard",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="whiteboardsnapshot",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(("class_session__isnull", False), ("user__isnull", True))
                    | models.Q(("class_session__isnull", True), ("user__isnull", False))
                ),
                name="whiteboard_exactly_one_of_session_or_user",
            ),
        ),
    ]
