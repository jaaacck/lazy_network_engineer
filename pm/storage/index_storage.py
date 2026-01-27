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
from pm.models import Entity, Update, Status, Person, EntityPerson, Label, EntityLabel, ensure_index_tables

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
                    'status': metadata.get('status', ''),  # Keep old field for backward compatibility
                    'priority': metadata.get('priority'),
                    'created': metadata.get('created', ''),
                    'updated': metadata.get('updated', ''),
                    'due_date': metadata.get('due_date', ''),  # Keep old field for backward compatibility
                    'schedule_start': metadata.get('schedule_start', ''),  # Keep old field for backward compatibility
                    'schedule_end': metadata.get('schedule_end', ''),  # Keep old field for backward compatibility
                    'content': content or '',
                    'metadata_json': json.dumps(metadata),  # Keep for backward compatibility
                    # New extracted metadata fields
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
                
                # Set Status ForeignKey if status is provided
                status_name = metadata.get('status', '')
                if status_name:
                    try:
                        # Find status that applies to this entity type
                        status_obj = Status.objects.filter(
                            name=status_name,
                            is_active=True
                        ).filter(
                            Q(entity_types__contains=entity_type) | Q(entity_types__contains='all')
                        ).first()
                        
                        if status_obj:
                            entity_data['status_fk'] = status_obj
                    except Exception as e:
                        logger.warning(f"Could not find status '{status_name}' for entity type '{entity_type}': {e}")
                
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
                
                # Set relationships based on type
                if entity_type == 'epic':
                    entity_data['project_id'] = metadata.get('project_id', '')
                elif entity_type == 'task':
                    entity_data['project_id'] = metadata.get('project_id', '')
                    entity_data['epic_id'] = metadata.get('epic_id', '')
                elif entity_type == 'subtask':
                    entity_data['project_id'] = metadata.get('project_id', '')
                    entity_data['epic_id'] = metadata.get('epic_id', '')
                    entity_data['task_id'] = metadata.get('task_id', '')
                
                # Update or create entity
                entity, created = Entity.objects.update_or_create(
                    id=entity_id,
                    defaults=entity_data
                )
                
                # Update EntityPerson relationships
                self._sync_entity_persons(entity, people_tags or [])
                
                # Update EntityLabel relationships
                self._sync_entity_labels(entity, labels or [])
                
                # Update search index
                self._update_search_index(entity_id, metadata.get('title', ''), content or '', 
                                        updates_text, people_tags or [], labels or [])
                
                # Update relationships
                self._update_relationships(entity_id, entity_type, metadata)
                
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
    
    def _update_relationships(self, entity_id, entity_type, metadata):
        """Update relationships table."""
        with connection.cursor() as cursor:
            # Delete existing relationships for this entity
            cursor.execute("DELETE FROM relationships WHERE child_id = %s", [entity_id])
            
            # Add parent relationships
            if entity_type == 'epic' and metadata.get('project_id'):
                cursor.execute("""
                    INSERT OR REPLACE INTO relationships (parent_id, child_id, type)
                    VALUES (%s, %s, %s)
                """, [metadata['project_id'], entity_id, 'epic'])
            elif entity_type == 'task' and metadata.get('epic_id'):
                cursor.execute("""
                    INSERT OR REPLACE INTO relationships (parent_id, child_id, type)
                    VALUES (%s, %s, %s)
                """, [metadata['epic_id'], entity_id, 'task'])
            elif entity_type == 'subtask' and metadata.get('task_id'):
                cursor.execute("""
                    INSERT OR REPLACE INTO relationships (parent_id, child_id, type)
                    VALUES (%s, %s, %s)
                """, [metadata['task_id'], entity_id, 'subtask'])
    
    def _sync_entity_persons(self, entity, people_tags):
        """Sync EntityPerson relationships."""
        # Normalize people tags (remove @ prefix, lowercase for lookup)
        normalized_tags = [tag.strip().lstrip('@').lower() for tag in people_tags if tag.strip()]
        
        # Get current Person IDs that should be assigned
        person_ids_to_keep = set()
        for tag in normalized_tags:
            person = Person.objects.filter(name__iexact=tag).first()
            if person:
                person_ids_to_keep.add(person.id)
        
        # Get current relationships
        current_relationships = EntityPerson.objects.filter(entity=entity)
        current_person_ids = {ep.person_id for ep in current_relationships}
        
        # Add new relationships
        for person_id in person_ids_to_keep - current_person_ids:
            EntityPerson.objects.get_or_create(
                entity=entity,
                person_id=person_id
            )
        
        # Remove relationships that are no longer in the list
        EntityPerson.objects.filter(entity=entity).exclude(person_id__in=person_ids_to_keep).delete()
    
    def _sync_entity_labels(self, entity, labels_list):
        """Sync EntityLabel relationships."""
        # Normalize labels (handle both string and list formats)
        if isinstance(labels_list, str):
            normalized_labels = [l.strip().lower() for l in labels_list.split(',') if l.strip()]
        elif isinstance(labels_list, list):
            normalized_labels = [str(l).strip().lower() for l in labels_list if str(l).strip()]
        else:
            normalized_labels = []
        
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
        current_relationships = EntityLabel.objects.filter(entity=entity)
        current_label_ids = {el.label_id for el in current_relationships}
        
        # Add new relationships
        for label_id in label_ids_to_keep - current_label_ids:
            EntityLabel.objects.get_or_create(
                entity=entity,
                label_id=label_id
            )
        
        # Remove relationships that are no longer in the list
        EntityLabel.objects.filter(entity=entity).exclude(label_id__in=label_ids_to_keep).delete()
    
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
        try:
            result = Entity.objects.get(id=entity_id)
            return result
        except Entity.DoesNotExist:
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
                    try:
                        entity = Entity.objects.get(id=entity_id)
                        results.append({
                            'entity': entity,
                            'title_match': row[1] or '',
                            'content_match': row[2] or '',
                            'updates_match': row[3] or '',
                            'people_match': row[4] or '',
                            'labels_match': row[5] or '',
                        })
                    except Entity.DoesNotExist:
                        continue
        except Exception as e:
            logger.error(f"FTS5 search error: {e}")
            return []
        
        return results
    
    def query_entities(self, entity_type=None, status=None, project_id=None, 
                      epic_id=None, due_date_start=None, due_date_end=None):
        """Query entities with filters."""
        qs = Entity.objects.all()
        
        if entity_type:
            qs = qs.filter(type=entity_type)
        if status:
            # Try to use status_fk first, fall back to old status field
            try:
                status_obj = Status.objects.filter(name=status, is_active=True).first()
                if status_obj:
                    qs = qs.filter(status_fk=status_obj)
                else:
                    qs = qs.filter(status=status)  # Fallback to old field
            except Exception:
                qs = qs.filter(status=status)  # Fallback to old field
        if project_id:
            qs = qs.filter(project_id=project_id)
        if epic_id:
            qs = qs.filter(epic_id=epic_id)
        if due_date_start:
            # Try new date field first, fall back to old string field
            qs = qs.filter(Q(due_date_dt__gte=due_date_start) | Q(due_date__gte=due_date_start))
        if due_date_end:
            qs = qs.filter(Q(due_date_dt__lte=due_date_end) | Q(due_date__lte=due_date_end))
        
        return qs
    
    def delete_entity(self, entity_id):
        """Delete entity from index."""
        try:
            with transaction.atomic():
                # Delete EntityPerson relationships (CASCADE should handle this, but explicit is better)
                EntityPerson.objects.filter(entity_id=entity_id).delete()
                
                # Delete EntityLabel relationships (CASCADE should handle this, but explicit is better)
                EntityLabel.objects.filter(entity_id=entity_id).delete()
                
                # Delete from search index
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM search_index WHERE entity_id = %s", [entity_id])
                    cursor.execute("DELETE FROM relationships WHERE parent_id = %s OR child_id = %s", 
                                 [entity_id, entity_id])
                    cursor.execute("DELETE FROM updates WHERE entity_id = %s", [entity_id])
                
                # Delete entity
                Entity.objects.filter(id=entity_id).delete()
        except Exception as e:
            logger.error(f"Error deleting entity {entity_id} from index: {e}")
            raise
