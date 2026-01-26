"""
Index storage layer - SQLite index operations (performance layer).
"""
import json
import logging
from django.db import connection, transaction
from django.db.models import Q
from pm.models import Entity, Update, ensure_index_tables

logger = logging.getLogger('pm')


class IndexStorage:
    """Handles SQLite index operations."""
    
    def __init__(self):
        ensure_index_tables()
    
    def sync_entity(self, entity_id, entity_type, file_path, file_mtime, metadata, content=None, 
                    updates_text='', people_tags=None, labels=None):
        """Sync an entity to the SQLite index."""
        try:
            with transaction.atomic():
                # Prepare entity data
                entity_data = {
                    'id': entity_id,
                    'type': entity_type,
                    'title': metadata.get('title', 'Untitled'),
                    'status': metadata.get('status', ''),
                    'priority': metadata.get('priority'),
                    'created': metadata.get('created', ''),
                    'updated': metadata.get('updated', ''),
                    'due_date': metadata.get('due_date', ''),
                    'schedule_start': metadata.get('schedule_start', ''),
                    'schedule_end': metadata.get('schedule_end', ''),
                    'file_path': file_path,
                    'file_mtime': file_mtime,
                    'metadata_json': json.dumps(metadata),
                }
                
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
                Entity.objects.update_or_create(
                    id=entity_id,
                    defaults=entity_data
                )
                
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
    
    def _sync_updates(self, entity_id, updates_list):
        """Sync updates to updates table."""
        with connection.cursor() as cursor:
            # Delete existing updates
            cursor.execute("DELETE FROM updates WHERE entity_id = %s", [entity_id])
            
            # Insert updates
            for update in updates_list:
                cursor.execute("""
                    INSERT INTO updates (entity_id, content, timestamp)
                    VALUES (%s, %s, %s)
                """, [entity_id, update.get('content', ''), update.get('timestamp', '')])
    
    def get_entity(self, entity_id):
        """Get entity from index."""
        try:
            return Entity.objects.get(id=entity_id)
        except Entity.DoesNotExist:
            return None
    
    def is_stale(self, entity_id, file_mtime):
        """Check if index entry is stale compared to file."""
        try:
            entity = Entity.objects.get(id=entity_id)
            return entity.file_mtime < file_mtime
        except Entity.DoesNotExist:
            return True
    
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
            qs = qs.filter(status=status)
        if project_id:
            qs = qs.filter(project_id=project_id)
        if epic_id:
            qs = qs.filter(epic_id=epic_id)
        if due_date_start:
            qs = qs.filter(due_date__gte=due_date_start)
        if due_date_end:
            qs = qs.filter(due_date__lte=due_date_end)
        
        return qs
    
    def delete_entity(self, entity_id):
        """Delete entity from index."""
        try:
            with transaction.atomic():
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
