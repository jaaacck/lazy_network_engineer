# Generated migration to populate metadata fields from metadata_json

from django.db import migrations
import json
from django.utils.dateparse import parse_datetime


def populate_metadata_fields(apps, schema_editor):
    """Extract data from metadata_json and populate new fields."""
    Entity = apps.get_model('pm', 'Entity')
    Label = apps.get_model('pm', 'Label')
    EntityLabel = apps.get_model('pm', 'EntityLabel')
    Update = apps.get_model('pm', 'Update')
    
    # Process all entities
    for entity in Entity.objects.all():
        if not entity.metadata_json:
            continue
        
        try:
            metadata = json.loads(entity.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue
        
        # Extract simple fields
        if 'seq_id' in metadata:
            entity.seq_id = metadata.get('seq_id', '')
        if 'archived' in metadata:
            entity.archived = metadata.get('archived', False)
        if 'is_inbox_epic' in metadata:
            entity.is_inbox_epic = metadata.get('is_inbox_epic', False)
        if 'color' in metadata:
            entity.color = metadata.get('color', '')
        
        # Extract JSON fields
        if 'notes' in metadata:
            entity.notes = metadata.get('notes', [])
        if 'dependencies' in metadata:
            entity.dependencies = metadata.get('dependencies', [])
        if 'checklist' in metadata:
            entity.checklist = metadata.get('checklist', [])
        if 'stats' in metadata:
            entity.stats = metadata.get('stats', {})
        if 'stats_version' in metadata:
            entity.stats_version = metadata.get('stats_version')
        if 'stats_updated' in metadata:
            stats_updated_str = metadata.get('stats_updated', '')
            if stats_updated_str:
                parsed = parse_datetime(stats_updated_str)
                if parsed:
                    entity.stats_updated = parsed
        
        # Save entity with new fields
        entity.save()
        
        # Extract and create labels
        labels_list = metadata.get('labels', [])
        if labels_list:
            # Normalize labels (handle both string and list formats)
            if isinstance(labels_list, str):
                labels_list = [l.strip() for l in labels_list.split(',') if l.strip()]
            elif isinstance(labels_list, list):
                labels_list = [str(l).strip() for l in labels_list if str(l).strip()]
            
            # Create Label records and EntityLabel relationships
            for label_name in labels_list:
                if not label_name:
                    continue
                
                # Get or create Label
                label, created = Label.objects.get_or_create(
                    name=label_name.lower(),  # Normalize to lowercase
                    defaults={'name': label_name.lower()}
                )
                
                # Create EntityLabel relationship if it doesn't exist
                EntityLabel.objects.get_or_create(
                    entity=entity,
                    label=label
                )
        
        # Migrate updates to Update table
        updates_list = metadata.get('updates', [])
        if updates_list:
            for update_entry in updates_list:
                if isinstance(update_entry, dict):
                    timestamp = update_entry.get('timestamp', '')
                    content = update_entry.get('content', '')
                    if timestamp and content:
                        # Check if update already exists
                        Update.objects.get_or_create(
                            entity_id=entity.id,
                            timestamp=timestamp,
                            defaults={'content': content}
                        )


def reverse_populate_metadata_fields(apps, schema_editor):
    """Reverse migration - this would require reconstructing metadata_json, but we'll keep it simple."""
    # For reverse migration, we could reconstruct metadata_json from the new fields
    # but since metadata_json is kept for backward compatibility, we'll just pass
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0005_extract_metadata_fields'),
    ]

    operations = [
        migrations.RunPython(populate_metadata_fields, reverse_populate_metadata_fields),
    ]
