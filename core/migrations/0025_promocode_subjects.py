from django.db import migrations, models


def copy_subject_to_subjects(apps, schema_editor):
    """Reprend l'ancien lien simple (subject) dans le nouveau champ multiple (subjects), pour ne perdre aucun code promo existant."""
    PromoCode = apps.get_model("core", "PromoCode")
    for promo in PromoCode.objects.exclude(subject__isnull=True):
        promo.subjects.add(promo.subject_id)


def noop_reverse(apps, schema_editor):
    """Pas de retour en arrière automatique : un code promo pourrait avoir plusieurs matières une fois la migration appliquée, ce qui ne rentre plus dans l'ancien champ à une seule matière."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_infosessionsignup"),
    ]

    operations = [
        migrations.AddField(
            model_name="promocode",
            name="subjects",
            field=models.ManyToManyField(blank=True, related_name="promo_codes", to="core.subject"),
        ),
        migrations.RunPython(copy_subject_to_subjects, noop_reverse),
        migrations.RemoveField(
            model_name="promocode",
            name="subject",
        ),
    ]
