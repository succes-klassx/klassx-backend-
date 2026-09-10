import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.
    CreateModel simple, vérifiée à la main contre models.ChatTutoringPlan/
    ChatTutoringSubscription/ChatThread/ChatMessage.
    """

    dependencies = [
        ("core", "0022_pack_packpurchase"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatTutoringPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("description", models.TextField(blank=True)),
                ("max_questions_per_month", models.PositiveIntegerField(default=10)),
                ("price_cents", models.PositiveIntegerField()),
                ("price_millimes_tnd", models.PositiveIntegerField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assigned_teacher",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="chat_tutoring_plans",
                        to="core.teacherprofile",
                    ),
                ),
                (
                    "subject",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+", to="core.subject",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ChatTutoringSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(
                    choices=[("pending", "En attente de paiement"), ("active", "Actif"), ("cancelled", "Annulé")],
                    default="pending", max_length=10,
                )),
                ("questions_used_this_period", models.PositiveIntegerField(default=0)),
                ("period_started_at", models.DateTimeField(auto_now_add=True)),
                ("stripe_subscription_id", models.CharField(blank=True, max_length=200)),
                ("free_question_used", models.BooleanField(default=False)),
                ("free_question_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="subscriptions",
                        to="core.chattutoringplan",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="chat_tutoring_subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="chattutoringsubscription",
            constraint=models.UniqueConstraint(fields=("plan", "student"), name="one_chat_subscription_per_plan_per_student"),
        ),
        migrations.CreateModel(
            name="ChatThread",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "subscription",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, related_name="thread",
                        to="core.chattutoringsubscription",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ChatMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("content", models.TextField(blank=True)),
                ("attachment", models.FileField(blank=True, upload_to="chat_attachments/%Y/%m/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "sender",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="chat_messages_sent",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "thread",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="messages",
                        to="core.chatthread",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
    ]
