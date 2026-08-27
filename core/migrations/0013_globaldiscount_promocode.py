from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_alter_grouprequest_weekly_hours_labels_v2'),
    ]

    operations = [
        migrations.CreateModel(
            name='GlobalDiscount',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('percentage', models.PositiveSmallIntegerField(default=0, help_text='0 à 100.')),
                ('is_active', models.BooleanField(default=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='PromoCode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=32, unique=True)),
                ('percentage', models.PositiveSmallIntegerField(help_text='1 à 100.')),
                ('is_active', models.BooleanField(default=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True, help_text='Laisser vide pour ne jamais expirer.')),
                ('max_uses', models.PositiveIntegerField(blank=True, null=True, help_text="Laisser vide pour un nombre illimité d'utilisations.")),
                ('times_used', models.PositiveIntegerField(default=0, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
