from django.db import models
from django.db import connection
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
import json
import os
from django.conf import settings


class Status(models.Model):
    """Status definitions for entities."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, unique=True)  # 'todo', 'in_progress', etc.
    display_name = models.CharField(max_length=100)  # 'Todo', 'In Progress', etc.
    entity_types = models.CharField(max_length=200)  # Comma-separated: 'task,subtask'
    color = models.CharField(max_length=7, blank=True)  # Hex color for UI
    order = models.IntegerField(default=0)  # Display ordering
    is_active = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'statuses'
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.display_name


class Person(models.Model):
    """Person/team member records."""
    id = models.CharField(max_length=50, primary_key=True)  # Keep 'person-xxxx' format
    name = models.CharField(max_length=200, unique=True)  # Normalized name
    display_name = models.CharField(max_length=200, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    job_title = models.CharField(max_length=200, blank=True)
    company = models.CharField(max_length=200, blank=True)
    notes = models.JSONField(default=list, blank=True)  # Array of note IDs linked to this person
    content = models.TextField(blank=True)  # Ad-hoc notes content (markdown)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    metadata_json = models.TextField(blank=True)  # For backward compatibility
    
    class Meta:
        db_table = 'persons'
        indexes = [
            models.Index(fields=['name']),
        ]
    
    def __str__(self):
        return self.display_name or self.name


class Label(models.Model):
    """Label definitions for entities."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200, unique=True)  # Normalized label name
    created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'labels'
        indexes = [
            models.Index(fields=['name']),
        ]
    
    def __str__(self):
        return self.name


# =============================================================================
# OLD MODELS - DEPRECATED (Removed after migration to specialized models)
# Entity, EntityPerson, EntityLabel have been replaced by:
# Project, Epic, Task, Subtask, Note, EntityPersonLink, EntityLabelLink
# =============================================================================


class Update(models.Model):
    """Index of updates for activity feeds."""
    id = models.AutoField(primary_key=True)
    entity_id = models.CharField(max_length=50)
    content = models.TextField()
    timestamp = models.CharField(max_length=50)
    type = models.CharField(max_length=20, default='user', db_index=True)  # 'system' or 'user'
    activity_type = models.CharField(max_length=50, blank=True, null=True)  # e.g., 'status_changed', 'label_added'
    
    class Meta:
        db_table = 'updates'
        indexes = [
            models.Index(fields=['entity_id']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['entity_id', 'activity_type']),  # For filtered queries
        ]


# =============================================================================
# NEW: Refactored Entity Models with Type-Specific Classes
# =============================================================================

class BaseEntity(models.Model):
    """Abstract base class for all entity types with common fields."""
    id = models.CharField(max_length=50, primary_key=True)
    title = models.CharField(max_length=500)
    status_fk = models.ForeignKey(Status, on_delete=models.PROTECT, related_name='%(class)s_entities')
    priority = models.IntegerField(null=True, blank=True)
    created = models.CharField(max_length=50, blank=True)
    updated = models.CharField(max_length=50, blank=True)
    
    # Scheduling fields
    due_date_dt = models.DateField(null=True, blank=True)
    schedule_start_dt = models.DateTimeField(null=True, blank=True)
    schedule_end_dt = models.DateTimeField(null=True, blank=True)
    
    # Content
    content = models.TextField(blank=True)
    
    # Extracted metadata fields
    seq_id = models.CharField(max_length=50, blank=True, null=True)
    archived = models.BooleanField(default=False)
    
    class Meta:
        abstract = True
    
    def __str__(self):
        return self.title


class Project(BaseEntity):
    """Project entity - top-level organizational unit."""
    # Project-specific fields
    color = models.CharField(max_length=7, blank=True, null=True)  # Hex color
    stats = models.JSONField(default=dict, blank=True)  # Aggregated statistics
    stats_version = models.IntegerField(null=True, blank=True)
    stats_updated = models.DateTimeField(null=True, blank=True)
    notes = models.JSONField(default=list, blank=True)  # Array of note IDs
    
    class Meta:
        db_table = 'pm_project'
        indexes = [
            models.Index(fields=['status_fk']),
            models.Index(fields=['archived']),
            models.Index(fields=['seq_id']),
        ]


class Epic(BaseEntity):
    """Epic entity - belongs to a project, contains tasks."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='epics')
    is_inbox_epic = models.BooleanField(default=False)
    notes = models.JSONField(default=list, blank=True)  # Array of note IDs
    
    class Meta:
        db_table = 'pm_epic'
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['status_fk']),
            models.Index(fields=['archived']),
            models.Index(fields=['is_inbox_epic']),
        ]


class Task(BaseEntity):
    """Task entity - belongs to a project, optionally to an epic."""
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    epic = models.ForeignKey(Epic, on_delete=models.CASCADE, related_name='tasks', null=True, blank=True)
    
    # Task-specific fields
    dependencies = models.JSONField(default=dict, blank=True)  # Dict with blocks/blocked_by lists
    checklist = models.JSONField(default=list, blank=True)  # Checklist items
    notes = models.JSONField(default=list, blank=True)  # Array of note IDs
    
    class Meta:
        db_table = 'pm_task'
        indexes = [
            models.Index(fields=['project']),
            models.Index(fields=['epic']),
            models.Index(fields=['status_fk']),
            models.Index(fields=['due_date_dt']),
            models.Index(fields=['archived']),
        ]


class Subtask(BaseEntity):
    """Subtask entity - belongs to a task (and inherits project/epic from task)."""
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='subtasks')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='subtasks')
    epic = models.ForeignKey(Epic, on_delete=models.CASCADE, related_name='subtasks', null=True, blank=True)
    
    # Subtask-specific fields
    dependencies = models.JSONField(default=dict, blank=True)  # Dict with blocks/blocked_by lists
    checklist = models.JSONField(default=list, blank=True)  # Checklist items
    notes = models.JSONField(default=list, blank=True)  # Array of note IDs
    
    class Meta:
        db_table = 'pm_subtask'
        indexes = [
            models.Index(fields=['task']),
            models.Index(fields=['project']),
            models.Index(fields=['epic']),
            models.Index(fields=['status_fk']),
            models.Index(fields=['due_date_dt']),
            models.Index(fields=['archived']),
        ]


class Note(BaseEntity):
    """Note entity - standalone notes."""
    notes = models.JSONField(default=list, blank=True)  # Array of linked note IDs
    
    class Meta:
        db_table = 'pm_note'
        indexes = [
            models.Index(fields=['status_fk']),
            models.Index(fields=['archived']),
        ]


class JournalEntry(models.Model):
    """Daily work journal entries."""
    date = models.DateField(unique=True, db_index=True)
    content = models.TextField(blank=True)
    linked_entities = models.JSONField(default=dict, blank=True)  # Store referenced entity IDs
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'pm_journal_entry'
        ordering = ['-date']
        verbose_name_plural = 'Journal Entries'
    
    def __str__(self):
        return f"Journal Entry {self.date}"


# =============================================================================
# Many-to-Many Relationships using GenericForeignKey
# =============================================================================

class EntityPersonLink(models.Model):
    """Generic many-to-many relationship between any entity type and Person."""
    # GenericForeignKey to any entity type
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=50)
    entity = GenericForeignKey('content_type', 'object_id')
    
    # Person relationship
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='entity_links')
    created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'pm_entity_person_link'
        unique_together = [['content_type', 'object_id', 'person']]
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['person']),
        ]
    
    def __str__(self):
        return f"{self.entity} - {self.person.name}"


class EntityLabelLink(models.Model):
    """Generic many-to-many relationship between any entity type and Label."""
    # GenericForeignKey to any entity type
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=50)
    entity = GenericForeignKey('content_type', 'object_id')
    
    # Label relationship
    label = models.ForeignKey(Label, on_delete=models.CASCADE, related_name='entity_links')
    created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'pm_entity_label_link'
        unique_together = [['content_type', 'object_id', 'label']]
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
            models.Index(fields=['label']),
        ]
    
    def __str__(self):
        return f"{self.entity} - {self.label.name}"


def init_search_index():
    """Initialize FTS5 search index if it does not exist."""
    with connection.cursor() as cursor:
        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='search_index'
        """)
        if cursor.fetchone():
            return
        
        # Create FTS5 virtual table
        cursor.execute("""
            CREATE VIRTUAL TABLE search_index USING fts5(
                entity_id UNINDEXED,
            entity_type UNINDEXED,
                labels
            )
        """)


def ensure_index_tables():
    """Ensure all index tables exist."""
    init_search_index()
    # Note: relationships table was deprecated and dropped in migration 0015
    # Relationships are now handled by Django ForeignKeys in specialized models


# =============================================================================
# Django Signals for Automatic Search Index Updates
# =============================================================================

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
import logging

logger = logging.getLogger(__name__)


def _get_entity_data_for_indexing(instance):
    """Extract search-indexable data from an entity instance.
    
    Returns tuple: (metadata_dict, content_str, updates_text, people_tags, labels)
    """
    # Import here to avoid circular dependency
    from pm.views import _build_metadata_from_entity, _merge_people_from_entityperson
    
    try:
        # Build metadata from entity
        metadata = _build_metadata_from_entity(instance)
        metadata = _merge_people_from_entityperson(instance, metadata)
        
        # Get content
        content = instance.content if hasattr(instance, 'content') else ''
        
        # Get updates from Update table
        updates = Update.objects.filter(entity_id=instance.id).values_list('content', flat=True)
        updates_text = ' '.join(updates)
        
        # Get people tags from EntityPersonLink
        from django.contrib.contenttypes.models import ContentType
        content_type = ContentType.objects.get_for_model(instance)
        person_links = EntityPersonLink.objects.filter(
            content_type=content_type,
            object_id=instance.id
        ).select_related('person')
        people_tags = [link.person.name for link in person_links]
        
        # Get labels from EntityLabelLink
        label_links = EntityLabelLink.objects.filter(
            content_type=content_type,
            object_id=instance.id
        ).select_related('label')
        labels = [link.label.name for link in label_links]
        
        return metadata, content, updates_text, people_tags, labels
    except Exception as e:
        logger.error(f"Error extracting entity data for indexing: {e}")
        return None, None, '', [], []


@receiver(post_save, sender=Project)
@receiver(post_save, sender=Epic)
@receiver(post_save, sender=Task)
@receiver(post_save, sender=Subtask)
@receiver(post_save, sender=Note)
def auto_update_search_index(sender, instance, created, **kwargs):
    """Automatically update search index when entities are saved.
    
    This acts as a safety net to ensure search index stays in sync even if
    save_* functions in views.py are bypassed (e.g., bulk operations, admin edits).
    """
    # Skip if this is a raw save (e.g., from loaddata)
    if kwargs.get('raw', False):
        return
    
    # Determine entity type from model name
    entity_type = sender.__name__.lower()
    
    try:
        # Get indexable data
        metadata, content, updates_text, people_tags, labels = _get_entity_data_for_indexing(instance)
        
        if metadata is None:
            logger.warning(f"Could not extract metadata for {entity_type} {instance.id}, skipping search index update")
            return
        
        # Update search index
        from pm.storage.index_storage import IndexStorage
        index_storage = IndexStorage()
        index_storage._update_search_index(
            entity_id=instance.id,
            entity_type=entity_type,
            title=instance.title or '',
            content=content or '',
            updates_text=updates_text,
            people_tags=people_tags,
            labels=labels
        )
        
        logger.debug(f"Auto-updated search index for {entity_type} {instance.id}")
    except Exception as e:
        logger.error(f"Failed to auto-update search index for {entity_type} {instance.id}: {e}")


@receiver(post_delete, sender=Project)
@receiver(post_delete, sender=Epic)
@receiver(post_delete, sender=Task)
@receiver(post_delete, sender=Subtask)
@receiver(post_delete, sender=Note)
@receiver(post_delete, sender=JournalEntry)
def auto_cleanup_search_index(sender, instance, **kwargs):
    """Automatically remove entities from search index when deleted.
    
    This ensures orphaned search entries are cleaned up even if delete operations
    bypass the delete_entity() call.
    """
    entity_type = sender.__name__.lower()
    
    try:
        # Remove from search index and updates table
        with connection.cursor() as cursor:
            # For journal entries, use the synthetic entity_id format
            if entity_type == 'journalentry':
                entity_id = f"journal-{instance.date.strftime('%Y-%m-%d')}"
            else:
                entity_id = instance.id
            
            cursor.execute("DELETE FROM search_index WHERE entity_id = %s", [entity_id])
            if entity_type != 'journalentry':
                cursor.execute("DELETE FROM updates WHERE entity_id = %s", [instance.id])
        
        logger.debug(f"Auto-cleaned search index for deleted {entity_type} {instance.id}")
    except Exception as e:
        logger.error(f"Failed to auto-clean search index for {entity_type} {instance.id}: {e}")


@receiver(post_save, sender=JournalEntry)
def auto_update_journal_search_index(sender, instance, created, **kwargs):
    """Automatically update search index when journal entries are saved."""
    # Skip if this is a raw save (e.g., from loaddata)
    if kwargs.get('raw', False):
        return
    
    try:
        # Update search index for journal entry
        from pm.storage.index_storage import IndexStorage
        index_storage = IndexStorage()
        
        # Create synthetic title from date
        title = f"Journal - {instance.date.strftime('%B %d, %Y')}"
        
        # Use synthetic entity_id with date for consistency
        entity_id = f"journal-{instance.date.strftime('%Y-%m-%d')}"
        
        index_storage._update_search_index(
            entity_id=entity_id,
            entity_type='journalentry',
            title=title,
            content=instance.content or '',
            updates_text='',
            people_tags=[],
            labels=[]
        )
        
        logger.debug(f"Auto-updated search index for journal entry {instance.date}")
    except Exception as e:
        logger.error(f"Failed to auto-update search index for journal entry {instance.date}: {e}")

