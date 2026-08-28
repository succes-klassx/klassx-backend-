# Affiche maintenant les deux unites ensemble (ex: "1,5h/semaine
# (6h/mois)") au lieu du seul total mensuel, pour plus de clarte. Les
# valeurs stockees (4,6,8,12,16, toujours un total mensuel) ne changent
# pas - seul le libelle affiche est mis a jour.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_merge_20260827_1214'),
    ]

    operations = [
        migrations.AlterField(
            model_name='grouprequest',
            name='weekly_hours',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                choices=[
                    (4, '1h/semaine (4h/mois)'), (6, '1,5h/semaine (6h/mois)'), (8, '2h/semaine (8h/mois)'),
                    (12, '3h/semaine (12h/mois)'), (16, '4h/semaine (16h/mois)'),
                ],
            ),
        ),
    ]
