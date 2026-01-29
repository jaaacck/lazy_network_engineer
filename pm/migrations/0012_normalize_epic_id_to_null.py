# Generated migration to normalize epic_id to NULL

from django.db import migrations


def normalize_epic_id(apps, schema_editor):
    """Convert empty string epic_ids to NULL for tasks and subtasks."""
    Entity = apps.get_model('pm', 'Entity')
    
    # Set all empty string epic_ids to NULL for tasks
    tasks_updated = Entity.objects.filter(type='task', epic_id='').update(epic_id=None)
    print(f"Normalized {tasks_updated} tasks with empty epic_id to NULL")
    
    # Set all empty string epic_ids to NULL for subtasks
    subtasks_updated = Entity.objects.filter(type='subtask', epic_id='').update(epic_id=None)
    print(f"Normalized {subtasks_updated} subtasks with empty epic_id to NULL")


def reverse_normalize_epic_id(apps, schema_editor):
    """Reverse migration (for safety, but not recommended)."""
    # This is not reversible in a meaningful way, so we do nothing
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0011_add_person_fields'),
    ]

    operations = [
        migrations.RunPython(normalize_epic_id, reverse_normalize_epic_id),
    ]
