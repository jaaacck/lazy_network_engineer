# Migration to make status_fk NOT NULL

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0012_normalize_epic_id_to_null'),
    ]

    operations = [
        # Change status_fk from nullable to NOT NULL
        # This is safe because migration 0010 already ensured all entities have status_fk
        migrations.AlterField(
            model_name='entity',
            name='status_fk',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='entities',
                to='pm.status',
                null=False
            ),
        ),
    ]
