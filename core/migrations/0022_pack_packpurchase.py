import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.
    CreateModel simple, vérifiée à la main contre models.Pack/PackPurchase.
    """

    dependencies = [
        ("core", "0021_teacher_intro_video"),
    ]

    operations = [
        migrations.CreateModel(
            name="Pack",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("description", models.TextField(blank=True)),
                ("group_tier", models.CharField(max_length=10, choices=[
                    ("GROUP_10", "Groupe de 10"), ("GROUP_8", "Groupe de 8"), ("GROUP_6", "Groupe de 6"),
                    ("GROUP_5", "Groupe de 5"), ("GROUP_4", "Groupe de 4"), ("GROUP_3", "Groupe de 3"),
                    ("GROUP_2", "Groupe de 2"), ("INDIVIDUAL", "Individuel"),
                ])),
                ("total_hours", models.PositiveIntegerField()),
                ("price_cents", models.PositiveIntegerField()),
                ("price_millimes_tnd", models.PositiveIntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("order_index", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("subjects", models.ManyToManyField(related_name="packs", to="core.subject")),
            ],
            options={
                "ordering": ["order_index", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PackPurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hours_remaining", models.DecimalField(blank=True, decimal_places=1, max_digits=6, null=True)),
                ("status", models.CharField(choices=[("pending", "En attente de paiement"), ("paid", "Payé")], default="pending", max_length=10)),
                ("stripe_checkout_session_id", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("pack", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="core.pack")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pack_purchases", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
