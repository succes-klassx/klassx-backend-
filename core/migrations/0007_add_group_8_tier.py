# Adds the new "Group of 8" tier (12€/h) to every group_tier choices
# field. Choices are metadata only (not a DB constraint on CharField), so
# this doesn't rewrite any existing row — it just keeps the DB schema's
# recorded choices in sync with core/models.py.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_alter_payment_gateway_alter_selfstudyplan_code'),
    ]

    operations = [
        migrations.AlterField(
            model_name='grouprequest',
            name='group_tier',
            field=models.CharField(choices=[('GROUP_10', 'Group of 10'), ('GROUP_8', 'Group of 8'), ('GROUP_6', 'Group of 6'), ('GROUP_5', 'Group of 5'), ('GROUP_4', 'Group of 4'), ('GROUP_3', 'Group of 3'), ('GROUP_2', 'Group of 2'), ('INDIVIDUAL', 'Individual')], max_length=12),
        ),
        migrations.AlterField(
            model_name='groupassignment',
            name='group_tier',
            field=models.CharField(choices=[('GROUP_10', 'Group of 10'), ('GROUP_8', 'Group of 8'), ('GROUP_6', 'Group of 6'), ('GROUP_5', 'Group of 5'), ('GROUP_4', 'Group of 4'), ('GROUP_3', 'Group of 3'), ('GROUP_2', 'Group of 2'), ('INDIVIDUAL', 'Individual')], max_length=12),
        ),
        migrations.AlterField(
            model_name='classseries',
            name='group_tier',
            field=models.CharField(choices=[('GROUP_10', 'Group of 10'), ('GROUP_8', 'Group of 8'), ('GROUP_6', 'Group of 6'), ('GROUP_5', 'Group of 5'), ('GROUP_4', 'Group of 4'), ('GROUP_3', 'Group of 3'), ('GROUP_2', 'Group of 2'), ('INDIVIDUAL', 'Individual')], max_length=12),
        ),
        migrations.AlterField(
            model_name='classsession',
            name='group_tier',
            field=models.CharField(choices=[('GROUP_10', 'Group of 10'), ('GROUP_8', 'Group of 8'), ('GROUP_6', 'Group of 6'), ('GROUP_5', 'Group of 5'), ('GROUP_4', 'Group of 4'), ('GROUP_3', 'Group of 3'), ('GROUP_2', 'Group of 2'), ('INDIVIDUAL', 'Individual')], max_length=12),
        ),
        migrations.AlterField(
            model_name='pricingrate',
            name='group_tier',
            field=models.CharField(choices=[('GROUP_10', 'Groupe de 10'), ('GROUP_8', 'Groupe de 8'), ('GROUP_6', 'Groupe de 6'), ('GROUP_5', 'Groupe de 5'), ('GROUP_4', 'Groupe de 4'), ('GROUP_3', 'Groupe de 3'), ('GROUP_2', 'Groupe de 2'), ('INDIVIDUAL', 'Individuel')], max_length=12, unique=True),
        ),
    ]
