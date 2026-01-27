# Generated migration to remove legacy 'status' CharField

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0008_populate_update_types'),
    ]

    operations = [
        # Drop the index on status column first (SQLite has it but Django doesn't track it)
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS entities_status_1c41cf_idx;",
            reverse_sql="",  # No need to recreate in reverse
        ),
        # Remove the old status CharField
        migrations.RemoveField(
            model_name='entity',
            name='status',
        ),
    ]
