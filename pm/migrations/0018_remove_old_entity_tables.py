# Migration to remove old Entity model and related tables

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0017_migrate_entity_data'),
    ]

    operations = [
        # Drop the relationships table (has FK constraints to entities table)
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS relationships;",
            reverse_sql="",  # Cannot recreate automatically
        ),
        # Drop the old entity_persons junction table
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS entity_persons;",
            reverse_sql="",  # Cannot recreate automatically
        ),
        # Drop the old entity_labels junction table
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS entity_labels;",
            reverse_sql="",  # Cannot recreate automatically
        ),
        # Drop the old entities table
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS entities;",
            reverse_sql="",  # Cannot recreate automatically
        ),
    ]
