# Generated migration to fix search index

from django.db import migrations


def recreate_search_index(apps, schema_editor):
    """Drop and recreate search_index FTS5 table with entity_type column."""
    from django.db import connection
    
    with connection.cursor() as cursor:
        # Drop old search_index table if it exists
        cursor.execute("DROP TABLE IF EXISTS search_index")
        
        # Create new search_index table with entity_type column
        cursor.execute("""
            CREATE VIRTUAL TABLE search_index USING fts5(
                entity_id UNINDEXED,
                entity_type UNINDEXED,
                title,
                content,
                updates,
                people,
                labels
            )
        """)


def reverse_recreate_search_index(apps, schema_editor):
    """Reverse migration: drop new table and create old schema."""
    from django.db import connection
    
    with connection.cursor() as cursor:
        # Drop new search_index table
        cursor.execute("DROP TABLE IF EXISTS search_index")
        
        # Recreate old search_index table without entity_type
        cursor.execute("""
            CREATE VIRTUAL TABLE search_index USING fts5(
                entity_id UNINDEXED,
                title,
                content,
                updates,
                people,
                labels
            )
        """)


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0020_add_dependencies_to_subtask'),
    ]

    operations = [
        migrations.RunPython(recreate_search_index, reverse_recreate_search_index),
    ]
