"""
Synchronization manager between markdown files and SQLite index.
"""
import os
import json
import logging
from django.conf import settings
from .file_storage import FileStorage
from .index_storage import IndexStorage
from pm import utils

logger = logging.getLogger('pm')


class SyncManager:
    """Manages synchronization between files and index."""
    
    def __init__(self):
        self.file_storage = FileStorage()
        self.index_storage = IndexStorage()
    
    def sync_entity_to_index(self, file_path, entity_id, entity_type, metadata=None, content=None):
        """Sync a single entity from file to index."""
        if not os.path.exists(file_path):
            # File deleted, remove from index
            self.index_storage.delete_entity(entity_id)
            return
        
        # Load from file if not provided
        if metadata is None or content is None:
            default_title = f"Untitled {entity_type.title()}"
            default_status = 'active' if entity_type in ['project', 'epic'] else 'todo'
            metadata, content = self.file_storage.load_entity(
                file_path, default_title, default_status, metadata_only=False
            )
            if metadata is None:
                return
        
        file_mtime = self.file_storage.get_file_mtime(file_path)
        
        # Extract updates text
        updates_text = ' '.join([
            u.get('content', '') for u in metadata.get('updates', [])
        ])
        
        # Extract people and labels (for notes)
        people_tags = metadata.get('people', [])
        labels = metadata.get('labels', [])
        
        # Sync to index
        self.index_storage.sync_entity(
            entity_id=entity_id,
            entity_type=entity_type,
            file_path=file_path,
            file_mtime=file_mtime,
            metadata=metadata,
            content=content,
            updates_text=updates_text,
            people_tags=people_tags,
            labels=labels
        )
    
    def load_entity_with_index(self, file_path, entity_id, entity_type, 
                              default_title, default_status, metadata_only=False):
        """Load entity, checking index first, falling back to file."""
        # Try index first
        entity = self.index_storage.get_entity(entity_id)
        if entity:
            file_mtime = self.file_storage.get_file_mtime(file_path)
            if file_mtime and not self.index_storage.is_stale(entity_id, file_mtime):
                # Index is fresh, use it
                if metadata_only:
                    metadata = json.loads(entity.metadata_json)
                    return metadata, None
                else:
                    # Need full content, load from file
                    metadata, content = self.file_storage.load_entity(
                        file_path, default_title, default_status, metadata_only=False
                    )
                    return metadata, content
        
        # Index doesn't exist or is stale, load from file
        metadata, content = self.file_storage.load_entity(
            file_path, default_title, default_status, metadata_only
        )
        
        # Sync to index if we have data
        if metadata is not None:
            try:
                self.sync_entity_to_index(file_path, entity_id, entity_type, metadata, content)
            except Exception as e:
                logger.warning(f"Failed to sync {entity_id} to index: {e}")
        
        return metadata, content
    
    def save_entity_with_sync(self, file_path, entity_id, entity_type, metadata, content):
        """Save entity to file and sync to index."""
        # Save to file (source of truth)
        file_mtime = self.file_storage.save_entity(file_path, metadata, content)
        
        # Sync to index
        try:
            self.sync_entity_to_index(file_path, entity_id, entity_type, metadata, content)
        except Exception as e:
            logger.warning(f"Failed to sync {entity_id} to index after save: {e}")
            # Don't fail the save if index sync fails
        
        return file_mtime
