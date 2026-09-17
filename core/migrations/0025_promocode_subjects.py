from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_infosessionsignup"),
    ]

    operations = [
        migrations.AddField(
            model_name="promocode",
            name="subjects",
            field=models.ManyToManyField(blank=True, to="core.subject"),
        ),
    ]
