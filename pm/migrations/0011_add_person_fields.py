# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0010_ensure_status_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='phone',
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name='person',
            name='job_title',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='person',
            name='company',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='person',
            name='notes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='person',
            name='content',
            field=models.TextField(blank=True),
        ),
    ]
