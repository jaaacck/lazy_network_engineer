# Generated migration to add content field and remove file tracking

from django.db import migrations, models


def cleanup_orphaned_relationships(apps, schema_editor):
    """Clean up relationships that reference non-existent entities.
    No-op if the relationships table does not exist (e.g. fresh DB);
    that table is created at runtime by init_relationships_table(), not by migrations.
    """
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='relationships'
        """)
        if not cursor.fetchone():
            return
        cursor.execute("""
            DELETE FROM relationships
            WHERE parent_id NOT IN (SELECT id FROM entities)
        """)
        cursor.execute("""
            DELETE FROM relationships
            WHERE child_id NOT IN (SELECT id FROM entities)
        """)


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0001_initial'),
    ]

    operations = [
        # Clean up orphaned relationships first
        migrations.RunPython(cleanup_orphaned_relationships, migrations.RunPython.noop),
        # Add content field first (nullable/blank to allow migration)
        migrations.AddField(
            model_name='entity',
            name='content',
            field=models.TextField(blank=True),
        ),
        # Remove file tracking fields
        migrations.RemoveField(
            model_name='entity',
            name='file_path',
        ),
        migrations.RemoveField(
            model_name='entity',
            name='file_mtime',
        ),
    ]
