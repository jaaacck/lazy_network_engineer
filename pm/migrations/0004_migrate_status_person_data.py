# Generated migration to migrate status strings to ForeignKey and create EntityPerson relationships

from django.db import migrations
from django.utils.dateparse import parse_date, parse_datetime


def migrate_status_to_fk(apps, schema_editor):
    """Migrate Entity.status strings to Status ForeignKey."""
    Entity = apps.get_model('pm', 'Entity')
    Status = apps.get_model('pm', 'Status')
    
    # Map entity types to their valid status names
    status_map = {}
    
    # Get all statuses and build a map
    for status in Status.objects.all():
        entity_types = [t.strip() for t in status.entity_types.split(',')]
        for entity_type in entity_types:
            if entity_type not in status_map:
                status_map[entity_type] = {}
            status_map[entity_type][status.name] = status
    
    # Migrate each entity
    for entity in Entity.objects.all():
        if entity.status and entity.type in status_map:
            # Try to find matching status
            status_obj = status_map[entity.type].get(entity.status)
            if status_obj:
                entity.status_fk = status_obj
                entity.save(update_fields=['status_fk'])


def migrate_dates(apps, schema_editor):
    """Migrate Entity date strings to proper DateField/DateTimeField."""
    Entity = apps.get_model('pm', 'Entity')
    
    for entity in Entity.objects.all():
        # Migrate due_date
        if entity.due_date:
            try:
                # Try parsing as date first (YYYY-MM-DD)
                parsed_date = parse_date(entity.due_date)
                if parsed_date:
                    entity.due_date_dt = parsed_date
                    entity.save(update_fields=['due_date_dt'])
            except (ValueError, TypeError):
                # Try parsing as datetime (YYYY-MM-DDTHH:MM)
                try:
                    parsed_datetime = parse_datetime(entity.due_date)
                    if parsed_datetime:
                        entity.due_date_dt = parsed_datetime.date()
                        entity.save(update_fields=['due_date_dt'])
                except (ValueError, TypeError):
                    pass
        
        # Migrate schedule_start
        if entity.schedule_start:
            try:
                parsed_datetime = parse_datetime(entity.schedule_start)
                if parsed_datetime:
                    entity.schedule_start_dt = parsed_datetime
                    entity.save(update_fields=['schedule_start_dt'])
            except (ValueError, TypeError):
                pass
        
        # Migrate schedule_end
        if entity.schedule_end:
            try:
                parsed_datetime = parse_datetime(entity.schedule_end)
                if parsed_datetime:
                    entity.schedule_end_dt = parsed_datetime
                    entity.save(update_fields=['schedule_end_dt'])
            except (ValueError, TypeError):
                pass


def create_entity_person_relationships(apps, schema_editor):
    """Create EntityPerson relationships from metadata.people arrays."""
    Entity = apps.get_model('pm', 'Entity')
    Person = apps.get_model('pm', 'Person')
    EntityPerson = apps.get_model('pm', 'EntityPerson')
    import json
    
    # Build name to Person mapping
    person_map = {}
    for person in Person.objects.all():
        person_map[person.name.lower()] = person
        # Also map display_name if different
        if person.display_name and person.display_name.lower() != person.name.lower():
            person_map[person.display_name.lower()] = person
    
    # Process all entities
    for entity in Entity.objects.all():
        if not entity.metadata_json:
            continue
        
        try:
            metadata = json.loads(entity.metadata_json)
            people_list = metadata.get('people', [])
            
            if not people_list:
                continue
            
            # Normalize people list (handle both string and list formats)
            if isinstance(people_list, str):
                people_list = [p.strip().lstrip('@') for p in people_list.split(',') if p.strip()]
            elif isinstance(people_list, list):
                people_list = [str(p).strip().lstrip('@') for p in people_list if str(p).strip()]
            
            # Create EntityPerson relationships
            for person_name in people_list:
                person_name_normalized = person_name.lower()
                person = person_map.get(person_name_normalized)
                
                if person:
                    # Create relationship if it doesn't exist
                    EntityPerson.objects.get_or_create(
                        entity=entity,
                        person=person
                    )
        except (json.JSONDecodeError, TypeError):
            # Skip entities with invalid JSON
            continue


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0003_add_status_person_models'),
    ]

    operations = [
        migrations.RunPython(migrate_status_to_fk, migrations.RunPython.noop),
        migrations.RunPython(migrate_dates, migrations.RunPython.noop),
        migrations.RunPython(create_entity_person_relationships, migrations.RunPython.noop),
    ]
