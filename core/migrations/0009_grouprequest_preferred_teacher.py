from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_newslettersubscriber'),
    ]

    operations = [
        migrations.AddField(
            model_name='grouprequest',
            name='preferred_teacher',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='preferred_by_requests', to='core.teacherprofile',
            ),
        ),
    ]
