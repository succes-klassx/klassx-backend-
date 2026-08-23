import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main plutôt que générée par makemigrations : le
    renommage VideoCapsule -> SelfStudyContentItem déclenche une invite
    interactive ("avez-vous renommé ce modèle ?") que je ne peux pas
    piloter ici. Sans risque : ces 3 tables (VideoCapsule, VideoProgress,
    Subscription) sont vides au moment d'écrire cette migration — voir
    core/models.py pour la nouvelle structure (SelfStudyPlan,
    SelfStudyContentItem, Subscription restructurée en (user, plan)).
    """

    dependencies = [
        ("core", "0003_studentprofile_cecrl_level_subject_cecrl_level_and_more"),
    ]

    operations = [
        migrations.DeleteModel(name="VideoProgress"),
        migrations.DeleteModel(name="VideoCapsule"),
        migrations.DeleteModel(name="Subscription"),
        migrations.CreateModel(
            name="SelfStudyPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(choices=[
                    ("premiere_non_spe", "1ère — Tronc commun (non spécialité)"),
                    ("premiere_spe", "1ère — Spécialité Mathématiques"),
                    ("premiere_techno", "1ère Technologique"),
                    ("terminale_spe", "Terminale — Spécialité Mathématiques"),
                    ("terminale_maths_expertes", "Terminale — Mathématiques Expertes"),
                ], max_length=30, unique=True)),
                ("name", models.CharField(max_length=150)),
                ("price_cents", models.PositiveIntegerField(default=499, help_text="Prix mensuel en centimes d'euro (défaut : 4,99€).")),
                ("is_active", models.BooleanField(default=True, help_text="Décoché = plus proposé à l'abonnement (les abonnés existants gardent leur accès).")),
            ],
            options={"ordering": ["code"]},
        ),
        migrations.CreateModel(
            name="SelfStudyContentItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content_type", models.CharField(choices=[("video", "Vidéo"), ("pdf", "PDF")], max_length=5)),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("chapter_name", models.CharField(blank=True, max_length=150)),
                ("month", models.DateField(help_text="Premier jour du mois concerné, ex : 2026-09-01 pour le contenu de septembre.")),
                ("is_unlocked", models.BooleanField(default=False, help_text="Visible par les abonnés actifs de ce plan une fois coché.")),
                ("order_index", models.PositiveIntegerField(default=0)),
                ("video_provider_id", models.CharField(blank=True, max_length=200)),
                ("duration_seconds", models.PositiveIntegerField(blank=True, null=True)),
                ("pdf_file", models.FileField(blank=True, upload_to="selfstudy_pdfs/%Y/%m/")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="content_items", to="core.selfstudyplan")),
            ],
            options={"ordering": ["plan", "month", "chapter_name", "order_index"]},
        ),
        migrations.CreateModel(
            name="VideoProgress",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("progress_percentage", models.PositiveSmallIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("capsule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="progress_entries", to="core.selfstudycontentitem")),
                ("student", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="video_progress", to=settings.AUTH_USER_MODEL)),
            ],
            options={"unique_together": {("student", "capsule")}},
        ),
        migrations.CreateModel(
            name="Subscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("active", "Active"), ("past_due", "Past due"), ("cancelled", "Cancelled"), ("expired", "Expired")], default="active", max_length=12)),
                ("stripe_subscription_id", models.CharField(blank=True, max_length=255)),
                ("current_period_end", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subscriptions", to="core.selfstudyplan")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="selfstudy_subscriptions", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="subscription",
            constraint=models.UniqueConstraint(fields=("user", "plan"), name="unique_subscription_per_user_plan"),
        ),
    ]
