# Data migration to ensure all entities have valid status_fk before NOT NULL constraint

from django.db import migrations
from django.db.models import Q
import logging

logger = logging.getLogger('pm')


def ensure_all_entities_have_status_fk(apps, schema_editor):
    """Ensure all entities have a valid status_fk before making it NOT NULL."""
    Entity = apps.get_model('pm', 'Entity')
    Status = apps.get_model('pm', 'Status')
    
    entities_without_status = Entity.objects.filter(status_fk__isnull=True)
    
    if entities_without_status.exists():
        logger.warning(f"Found {entities_without_status.count()} entities without status_fk")
        
        for entity in entities_without_status:
            try:
                # Default statuses based on entity type
                default_statuses = {
                    'project': 'active',
                    'epic': 'active',
                    'task': 'todo',
                    'subtask': 'todo',
                    'note': 'active',
                    'person': 'active'
                }
                
                status_name = default_statuses.get(entity.type, 'todo')
                status = Status.objects.filter(
                    name=status_name,
                    is_active=True
                ).filter(
                    Q(entity_types__contains=entity.type) | Q(entity_types__contains='all')
                ).first()
                
                if status:
                    entity.status_fk = status
                    entity.save(update_fields=['status_fk'])
                    logger.info(f"Set {entity.id} status to {status_name}")
                else:
                    logger.error(f"Could not find status '{status_name}' for entity {entity.id} (type: {entity.type})")
            except Exception as e:
                logger.error(f"Error setting status for entity {entity.id}: {e}")
    else:
        logger.info("All entities have valid status_fk")


def reverse_func(apps, schema_editor):
    """No-op for reverse migration."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0009_remove_status_field'),
    ]

    operations = [
        migrations.RunPython(ensure_all_entities_have_status_fk, reverse_func),
    ]
