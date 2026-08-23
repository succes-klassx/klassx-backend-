from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_add_group_8_tier'),
    ]

    operations = [
        migrations.CreateModel(
            name='NewsletterSubscriber',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('subscribed_at', models.DateTimeField(auto_now_add=True)),
                ('synced_to_brevo', models.BooleanField(default=False)),
            ],
            options={
                'ordering': ['-subscribed_at'],
            },
        ),
    ]
