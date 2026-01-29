"""
Index storage layer - SQLite index operations (performance layer).
"""
import json
import logging
import time
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone as tz
from django.contrib.contenttypes.models import ContentType
from pm.models import (
    Update, Status, Person, Label,
    Project, Epic, Task, Subtask, Note, EntityPersonLink, EntityLabelLink,
    ensure_index_tables
)

logger = logging.getLogger('pm')


class IndexStorage:
    """Handles SQLite index operations."""
    
    _tables_ensured = False
    
    def __init__(self):
        # Only ensure tables once, not on every initialization
        if not IndexStorage._tables_ensured:
            ensure_index_tables()
            IndexStorage._tables_ensured = True
    
    def sync_entity(self, entity_id, entity_type, metadata, content=None, 
                    updates_text='', people_tags=None, labels=None):
        """Sync an entity to the SQLite database (primary storage)."""
        try:
            with transaction.atomic():
                # Prepare entity data
                entity_data = {
                    'id': entity_id,
                    'type': entity_type,
                    'title': metadata.get('title', 'Untitled'),
                    'priority': metadata.get('priority'),
                    'created': metadata.get('created', ''),
                    'updated': metadata.get('updated', ''),
                    'due_date': metadata.get('due_date', ''),
                    'schedule_start': metadata.get('schedule_start', ''),
                    'schedule_end': metadata.get('schedule_end', ''),
                    'content': content or '',
                    'metadata_json': json.dumps(metadata),
                    # Extracted metadata fields
                    'seq_id': metadata.get('seq_id', '') or None,
                    'archived': metadata.get('archived', False),
                    'is_inbox_epic': metadata.get('is_inbox_epic', False),
                    'color': metadata.get('color', '') or None,
                    'notes': metadata.get('notes', []),
                    'dependencies': metadata.get('dependencies', []),
                    'checklist': metadata.get('checklist', []),
                    'stats': metadata.get('stats', {}),
                    'stats_version': metadata.get('stats_version'),
                    'stats_updated': None,
                }
                
                # Parse stats_updated datetime if present
                if metadata.get('stats_updated'):
                    try:
                        parsed = parse_datetime(metadata['stats_updated'])
                        if parsed and settings.USE_TZ and tz.is_naive(parsed):
                            parsed = tz.make_aware(parsed, tz.get_current_timezone())
                        if parsed:
                            entity_data['stats_updated'] = parsed
                    except (ValueError, TypeError):
                        pass
                
                # Set Status ForeignKey - REQUIRED (no fallback to string status)
                status_name = metadata.get('status', '')
                if not status_name:
                    logger.error(f"No status provided for entity {entity_id}")
                    raise Exception(f"Status is required for entity {entity_id}")
                
                try:
                    # Find status that applies to this entity type
                    status_obj = Status.objects.filter(
                        name=status_name,
                        is_active=True
                    ).filter(
                        Q(entity_types__contains=entity_type) | Q(entity_types__contains='all')
                    ).first()
                    
                    if not status_obj:
                        logger.error(f"Could not find active status '{status_name}' for entity type '{entity_type}'")
                        raise Exception(f"Status '{status_name}' not found for entity type '{entity_type}'")
                    
                    entity_data['status_fk'] = status_obj
                except Exception as e:
                    logger.error(f"Status lookup failed for entity {entity_id}: {e}")
                    raise
                
                # Parse and set date fields
                if metadata.get('due_date'):
                    try:
                        parsed_date = parse_date(metadata['due_date'])
                        if not parsed_date:
                            parsed_datetime = parse_datetime(metadata['due_date'])
                            if parsed_datetime:
                                parsed_date = parsed_datetime.date()
                        if parsed_date:
                            entity_data['due_date_dt'] = parsed_date
                    except (ValueError, TypeError):
                        pass
                
                if metadata.get('schedule_start'):
                    try:
                        parsed_datetime = parse_datetime(metadata['schedule_start'])
                        if parsed_datetime:
                            entity_data['schedule_start_dt'] = parsed_datetime
                    except (ValueError, TypeError):
                        pass
                
                if metadata.get('schedule_end'):
                    try:
                        parsed_datetime = parse_datetime(metadata['schedule_end'])
                        if parsed_datetime:
                            entity_data['schedule_end_dt'] = parsed_datetime
                    except (ValueError, TypeError):
                        pass
                
                # Map entity type to model class
                model_map = {
                    'project': Project,
                    'epic': Epic,
                    'task': Task,
                    'subtask': Subtask,
                    'note': Note,
                }
                
                model_class = model_map.get(entity_type)
                if not model_class:
                    raise Exception(f"Unknown entity type: {entity_type}")
                
                # Build entity data based on model class
                # Common fields for all models
                base_entity_data = {
                    'id': entity_id,
                    'title': metadata.get('title', 'Untitled'),
                    'priority': metadata.get('priority'),
                    'created': metadata.get('created', ''),
                    'updated': metadata.get('updated', ''),
                    'content': content or '',
                    'seq_id': metadata.get('seq_id', '') or None,
                    'archived': metadata.get('archived', False),
                    'status_fk': None,  # Will be set below
                    'due_date_dt': None,
                    'schedule_start_dt': None,
                    'schedule_end_dt': None,
                }
                
                # Set Status ForeignKey - REQUIRED
                status_name = metadata.get('status', '')
                if not status_name:
                    logger.error(f"No status provided for entity {entity_id}")
                    raise Exception(f"Status is required for entity {entity_id}")
                
                try:
                    status_obj = Status.objects.filter(
                        name=status_name,
                        is_active=True
                    ).filter(
                        Q(entity_types__contains=entity_type) | Q(entity_types__contains='all')
                    ).first()
                    
                    if not status_obj:
                        logger.error(f"Could not find active status '{status_name}' for entity type '{entity_type}'")
                        raise Exception(f"Status '{status_name}' not found for entity type '{entity_type}'")
                    
                    base_entity_data['status_fk'] = status_obj
                except Exception as e:
                    logger.error(f"Status lookup failed for entity {entity_id}: {e}")
                    raise
                
                # Parse and set date fields
                if metadata.get('due_date'):
                    try:
                        parsed_date = parse_date(metadata['due_date'])
                        if not parsed_date:
                            parsed_datetime = parse_datetime(metadata['due_date'])
                            if parsed_datetime:
                                parsed_date = parsed_datetime.date()
                        if parsed_date:
                            base_entity_data['due_date_dt'] = parsed_date
                    except (ValueError, TypeError):
                        pass
                
                if metadata.get('schedule_start'):
                    try:
                        parsed_datetime = parse_datetime(metadata['schedule_start'])
                        if parsed_datetime:
                            base_entity_data['schedule_start_dt'] = parsed_datetime
                    except (ValueError, TypeError):
                        pass
                
                if metadata.get('schedule_end'):
                    try:
                        parsed_datetime = parse_datetime(metadata['schedule_end'])
                        if parsed_datetime:
                            base_entity_data['schedule_end_dt'] = parsed_datetime
                    except (ValueError, TypeError):
                        pass
                
                # Add type-specific fields and relationships
                if entity_type == 'project':
                    base_entity_data.update({
                        'color': metadata.get('color', '') or None,
                        'stats': metadata.get('stats', {}),
                        'stats_version': metadata.get('stats_version'),
                        'stats_updated': None,
                        'notes': metadata.get('notes', []),
                    })
                    if metadata.get('stats_updated'):
                        try:
                            parsed = parse_datetime(metadata['stats_updated'])
                            if parsed and settings.USE_TZ and tz.is_naive(parsed):
                                parsed = tz.make_aware(parsed, tz.get_current_timezone())
                            if parsed:
                                base_entity_data['stats_updated'] = parsed
                        except (ValueError, TypeError):
                            pass
                    
                elif entity_type == 'epic':
                    project_id = metadata.get('project_id', '')
                    if not project_id:
                        raise Exception(f"Epic {entity_id} requires project_id")
                    base_entity_data.update({
                        'project_id': project_id,
                        'is_inbox_epic': metadata.get('is_inbox_epic', False),
                        'notes': metadata.get('notes', []),
                    })
                    
                elif entity_type == 'task':
                    project_id = metadata.get('project_id', '')
                    if not project_id:
                        raise Exception(f"Task {entity_id} requires project_id")
                    base_entity_data.update({
                        'project_id': project_id,
                        'epic_id': metadata.get('epic_id') or None,
                        'dependencies': metadata.get('dependencies', []),
                        'checklist': metadata.get('checklist', []),
                        'notes': metadata.get('notes', []),
                    })
                    
                elif entity_type == 'subtask':
                    project_id = metadata.get('project_id', '')
                    task_id = metadata.get('task_id', '')
                    if not (project_id and task_id):
                        raise Exception(f"Subtask {entity_id} requires project_id and task_id")
                    base_entity_data.update({
                        'project_id': project_id,
                        'task_id': task_id,
                        'epic_id': metadata.get('epic_id') or None,
                        'dependencies': metadata.get('dependencies', {}),
                        'checklist': metadata.get('checklist', []),
                        'notes': metadata.get('notes', []),
                    })
                    
                elif entity_type == 'note':
                    base_entity_data.update({
                        'notes': metadata.get('notes', []),
                    })
                
                # Update or create entity
                entity, created = model_class.objects.update_or_create(
                    id=entity_id,
                    defaults=base_entity_data
                )
                
                # Update EntityPersonLink relationships (using GenericForeignKey)
                self._sync_entity_persons(entity, people_tags or [])
                
                # Update EntityLabelLink relationships (using GenericForeignKey)
                self._sync_entity_labels(entity, labels or [])
                
                # Update search index
                self._update_search_index(entity_id, metadata.get('title', ''), content or '', 
                                        updates_text, people_tags or [], labels or [])
                
                # Note: Relationships are now handled by Django ForeignKeys in the specialized models
                # The old relationships table was dropped in migration 0015
                
                # Update updates table
                self._sync_updates(entity_id, metadata.get('updates', []))
                
        except Exception as e:
            logger.error(f"Error syncing entity {entity_id} to index: {e}")
            raise
    
    def _update_search_index(self, entity_id, title, content, updates_text, people_tags, labels):
        """Update FTS5 search index."""
        with connection.cursor() as cursor:
            # Delete existing entry
            cursor.execute("DELETE FROM search_index WHERE entity_id = %s", [entity_id])
            
            # Insert new entry
            people_str = ' '.join(people_tags) if people_tags else ''
            labels_str = ' '.join(labels) if labels else ''
            
            cursor.execute("""
                INSERT INTO search_index (entity_id, title, content, updates, people, labels)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, [entity_id, title, content[:10000], updates_text[:10000], people_str, labels_str])
    
    
    def _sync_entity_persons(self, entity, people_tags):
        """Sync EntityPersonLink relationships using GenericForeignKey."""
        # Normalize people tags (remove @ prefix, lowercase for lookup)
        normalized_tags = [tag.strip().lstrip('@').lower() for tag in people_tags if tag.strip()]
        
        # Get content type for this entity
        content_type = ContentType.objects.get_for_model(entity)
        
        # Get current Person IDs that should be assigned
        person_ids_to_keep = set()
        for tag in normalized_tags:
            person = Person.objects.filter(name__iexact=tag).first()
            if person:
                person_ids_to_keep.add(person.id)
        
        # Get current relationships
        current_relationships = EntityPersonLink.objects.filter(
            content_type=content_type,
            object_id=entity.id
        )
        current_person_ids = {ep.person_id for ep in current_relationships}
        
        # Add new relationships
        for person_id in person_ids_to_keep - current_person_ids:
            EntityPersonLink.objects.get_or_create(
                content_type=content_type,
                object_id=entity.id,
                person_id=person_id
            )
        
        # Remove relationships that are no longer in the list
        EntityPersonLink.objects.filter(
            content_type=content_type,
            object_id=entity.id
        ).exclude(person_id__in=person_ids_to_keep).delete()
    
    def _sync_entity_labels(self, entity, labels_list):
        """Sync EntityLabelLink relationships using GenericForeignKey."""
        # Normalize labels (handle both string and list formats)
        if isinstance(labels_list, str):
            normalized_labels = [l.strip().lower() for l in labels_list.split(',') if l.strip()]
        elif isinstance(labels_list, list):
            normalized_labels = [str(l).strip().lower() for l in labels_list if str(l).strip()]
        else:
            normalized_labels = []
        
        # Get content type for this entity
        content_type = ContentType.objects.get_for_model(entity)
        
        # Get current Label IDs that should be assigned
        label_ids_to_keep = set()
        for label_name in normalized_labels:
            if not label_name:
                continue
            label, created = Label.objects.get_or_create(
                name=label_name.lower(),  # Normalize to lowercase
                defaults={'name': label_name.lower()}
            )
            label_ids_to_keep.add(label.id)
        
        # Get current relationships
        current_relationships = EntityLabelLink.objects.filter(
            content_type=content_type,
            object_id=entity.id
        )
        current_label_ids = {el.label_id for el in current_relationships}
        
        # Add new relationships
        for label_id in label_ids_to_keep - current_label_ids:
            EntityLabelLink.objects.get_or_create(
                content_type=content_type,
                object_id=entity.id,
                label_id=label_id
            )
        
        # Remove relationships that are no longer in the list
        EntityLabelLink.objects.filter(
            content_type=content_type,
            object_id=entity.id
        ).exclude(label_id__in=label_ids_to_keep).delete()
    
    def _sync_updates(self, entity_id, updates_list):
        """Sync updates to updates table."""
        # Build map of timestamp -> (type, activity_type) from existing Update table
        # This preserves the original type/activity_type when re-syncing
        update_map = {}
        try:
            stored_updates = Update.objects.filter(entity_id=entity_id).values('timestamp', 'type', 'activity_type')
            for u in stored_updates:
                update_map[u['timestamp']] = {
                    'type': u['type'],
                    'activity_type': u['activity_type']
                }
        except Exception as e:
            logger.warning(f"Could not load stored update types for {entity_id}: {e}")
        
        with connection.cursor() as cursor:
            # Delete existing updates
            cursor.execute("DELETE FROM updates WHERE entity_id = %s", [entity_id])
            
            # Insert updates, preserving stored types for existing updates
            for update in updates_list:
                timestamp = update.get('timestamp', '')
                
                # Use stored type/activity_type if this update already existed
                if timestamp and timestamp in update_map:
                    update_type = update_map[timestamp]['type']
                    activity_type = update_map[timestamp]['activity_type']
                else:
                    # For new updates, use the provided type or default to 'user'
                    update_type = update.get('type', 'user')
                    activity_type = update.get('activity_type', None)
                
                cursor.execute("""
                    INSERT INTO updates (entity_id, content, timestamp, type, activity_type)
                    VALUES (%s, %s, %s, %s, %s)
                """, [
                    entity_id, 
                    update.get('content', ''), 
                    timestamp,
                    update_type,
                    activity_type
                ])
    
    def get_entity(self, entity_id):
        """Get entity from index."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT entity_type FROM search_index WHERE entity_id = %s LIMIT 1", [entity_id])
            row = cursor.fetchone()

        if not row:
            return None

        entity_type = row[0]
        models = {
            'project': Project,
            'epic': Epic,
            'task': Task,
            'subtask': Subtask,
            'note': Note
        }
        model = models.get(entity_type)
        if not model:
            return None

        try:
            return model.objects.get(id=entity_id)
        except model.DoesNotExist:
            return None
    
    
    def search(self, query):
        """Full-text search using FTS5."""
        results = []
        # Format query for FTS5 - wrap in quotes for phrase search, or use OR for multiple terms
        fts_query = query.replace('"', '""')  # Escape quotes
        if ' ' in fts_query:
            # Multiple words - search for any of them
            terms = fts_query.split()
            fts_query = ' OR '.join([f'"{term}"' for term in terms])
        else:
            fts_query = f'"{fts_query}"'
        
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT entity_id, title, content, updates, people, labels
                    FROM search_index
                    WHERE search_index MATCH %s
                    ORDER BY rank
                    LIMIT 100
                """, [fts_query])
                
                for row in cursor.fetchall():
                    entity_id = row[0]
                    entity_obj = self.get_entity(entity_id)
                    if entity_obj:
                        results.append({
                            'entity': entity_obj,
                            'title_match': row[1] or '',
                            'content_match': row[2] or '',
                            'updates_match': row[3] or '',
                            'people_match': row[4] or '',
                            'labels_match': row[5] or '',
                        })
        except Exception as e:
            logger.error(f"FTS5 search error: {e}")
            return []
        
        return results
    
    def query_entities(self, entity_type=None, status=None, project_id=None, 
                      epic_id=None, due_date_start=None, due_date_end=None):
        """Query entities with filters - now works with specialized models."""
        all_results = []
        
        # Query based on entity type
        if entity_type in [None, 'project']:
            qs = Project.objects.all()
            if status:
                try:
                    status_obj = Status.objects.filter(name=status, is_active=True).first()
                    if status_obj:
                        qs = qs.filter(status_fk=status_obj)
                except:
                    pass
            if project_id:
                qs = qs.filter(id=project_id)
            all_results.extend(qs)
        
        if entity_type in [None, 'epic']:
            qs = Epic.objects.all()
            if status:
                try:
                    status_obj = Status.objects.filter(name=status, is_active=True).first()
                    if status_obj:
                        qs = qs.filter(status_fk=status_obj)
                except:
                    pass
            if project_id:
                qs = qs.filter(project_id=project_id)
            all_results.extend(qs)
        
        if entity_type in [None, 'task']:
            qs = Task.objects.all()
            if status:
                try:
                    status_obj = Status.objects.filter(name=status, is_active=True).first()
                    if status_obj:
                        qs = qs.filter(status_fk=status_obj)
                except:
                    pass
            if project_id:
                qs = qs.filter(project_id=project_id)
            if epic_id:
                qs = qs.filter(epic_id=epic_id)
            if due_date_start:
                qs = qs.filter(due_date_dt__gte=due_date_start)
            if due_date_end:
                qs = qs.filter(due_date_dt__lte=due_date_end)
            all_results.extend(qs)
        
        if entity_type in [None, 'subtask']:
            qs = Subtask.objects.all()
            if status:
                try:
                    status_obj = Status.objects.filter(name=status, is_active=True).first()
                    if status_obj:
                        qs = qs.filter(status_fk=status_obj)
                except:
                    pass
            if project_id:
                qs = qs.filter(project_id=project_id)
            if epic_id:
                qs = qs.filter(epic_id=epic_id)
            if due_date_start:
                qs = qs.filter(due_date_dt__gte=due_date_start)
            if due_date_end:
                qs = qs.filter(due_date_dt__lte=due_date_end)
            all_results.extend(qs)
        
        if entity_type in [None, 'note']:
            qs = Note.objects.all()
            if status:
                try:
                    status_obj = Status.objects.filter(name=status, is_active=True).first()
                    if status_obj:
                        qs = qs.filter(status_fk=status_obj)
                except:
                    pass
            all_results.extend(qs)
        
        return all_results
    
    def delete_entity(self, entity_id):
        """Delete entity from index and related data.
        
        Note: This method is deprecated. Entity deletion should be done via Django ORM
        on the specialized models (Project, Epic, Task, Subtask, Note), which will
        CASCADE delete related EntityPersonLink and EntityLabelLink records.
        """
        try:
            with transaction.atomic():
                # Delete from search index and updates
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM search_index WHERE entity_id = %s", [entity_id])
                    cursor.execute("DELETE FROM updates WHERE entity_id = %s", [entity_id])
                
                # Note: Entity model deletion should be handled via the specialized models
                # EntityPersonLink and EntityLabelLink will CASCADE delete automatically
        except Exception as e:
            logger.error(f"Error deleting entity {entity_id} from index: {e}")
            raise
