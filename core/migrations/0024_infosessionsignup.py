from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_chat_tutoring"),
    ]

    operations = [
        migrations.CreateModel(
            name="InfoSessionSignup",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("email", models.EmailField(max_length=254)),
                ("session_date", models.DateField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("synced_to_brevo", models.BooleanField(default=False)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
