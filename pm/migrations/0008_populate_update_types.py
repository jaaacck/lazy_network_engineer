# Data migration to populate type and activity_type fields in Update table
from django.db import migrations
import json


def populate_update_types(apps, schema_editor):
    """Populate type and activity_type fields from entity metadata JSON."""
    Entity = apps.get_model('pm', 'Entity')
    Update = apps.get_model('pm', 'Update')
    
    updated_count = 0
    matched_count = 0
    total_updates = Update.objects.count()
    
    print(f"\nPopulating type and activity_type for {total_updates} updates...")
    
    # Process each entity that has updates
    entities_with_updates = Entity.objects.filter(
        id__in=Update.objects.values_list('entity_id', flat=True).distinct()
    )
    
    for entity in entities_with_updates:
        try:
            # Parse metadata JSON
            if not entity.metadata_json:
                continue
                
            metadata = json.loads(entity.metadata_json)
            updates_in_metadata = metadata.get('updates', [])
            
            if not updates_in_metadata:
                continue
            
            # Get all Update records for this entity
            db_updates = Update.objects.filter(entity_id=entity.id)
            
            # Match each db update with metadata by timestamp
            for db_update in db_updates:
                # Find matching metadata update by timestamp
                matching_metadata = None
                for meta_update in updates_in_metadata:
                    if meta_update.get('timestamp') == db_update.timestamp:
                        matching_metadata = meta_update
                        break
                
                if matching_metadata:
                    # Extract type and activity_type from metadata
                    update_type = matching_metadata.get('type', 'user')
                    activity_type = matching_metadata.get('activity_type', None)
                    
                    # Update the database record
                    db_update.type = update_type
                    db_update.activity_type = activity_type
                    db_update.save(update_fields=['type', 'activity_type'])
                    
                    matched_count += 1
                    updated_count += 1
                else:
                    # No match found - leave as default 'user'
                    updated_count += 1
                    
        except json.JSONDecodeError:
            # Skip entities with invalid JSON
            continue
        except Exception as e:
            print(f"Error processing entity {entity.id}: {e}")
            continue
    
    print(f"✓ Processed {updated_count} updates")
    print(f"✓ Matched {matched_count} updates with metadata ({matched_count/total_updates*100:.1f}%)")
    print(f"✓ {total_updates - matched_count} updates defaulted to 'user' type\n")


def reverse_populate(apps, schema_editor):
    """Reverse migration - reset all types to default."""
    Update = apps.get_model('pm', 'Update')
    Update.objects.all().update(type='user', activity_type=None)


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0007_add_type_activity_type_to_update'),
    ]

    operations = [
        migrations.RunPython(populate_update_types, reverse_populate),
    ]
