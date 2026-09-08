from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Écrite à la main (comme les précédentes) — pas d'environnement Django
    complet disponible pour lancer makemigrations dans ce sandbox précis.
    CreateModel simple, sans ambiguïté : vérifiée à la main contre
    models.BlogPost.
    """

    dependencies = [
        ("core", "0016_whiteboard_personal"),
    ]

    operations = [
        migrations.CreateModel(
            name="BlogPost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=220, unique=True)),
                ("excerpt", models.CharField(blank=True, max_length=300)),
                ("content", models.TextField()),
                ("cover_image", models.ImageField(blank=True, upload_to="blog_covers/%Y/%m/")),
                ("author_name", models.CharField(blank=True, max_length=100)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-published_at"],
            },
        ),
    ]
