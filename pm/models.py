from django.db import models
from django.db import connection
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


class Entity(models.Model):
    """Index of all entities (projects, epics, tasks, subtasks, notes)."""
    id = models.CharField(max_length=50, primary_key=True)
    type = models.CharField(max_length=20)  # 'project', 'epic', 'task', 'subtask', 'note'
    title = models.CharField(max_length=500)
    # Status: keep old field for migration, add new ForeignKey
    status = models.CharField(max_length=50, blank=True)  # Old field, kept for migration
    status_fk = models.ForeignKey(Status, null=True, blank=True, on_delete=models.SET_NULL, related_name='entities')
    priority = models.IntegerField(null=True, blank=True)
    created = models.CharField(max_length=50, blank=True)
    updated = models.CharField(max_length=50, blank=True)
    
    # Relationships
    project_id = models.CharField(max_length=50, null=True, blank=True)
    epic_id = models.CharField(max_length=50, null=True, blank=True)
    task_id = models.CharField(max_length=50, null=True, blank=True)
    
    # Scheduling: keep old string fields for migration, add new date fields
    due_date = models.CharField(max_length=50, blank=True)  # Old field, kept for migration
    schedule_start = models.CharField(max_length=50, blank=True)  # Old field, kept for migration
    schedule_end = models.CharField(max_length=50, blank=True)  # Old field, kept for migration
    due_date_dt = models.DateField(null=True, blank=True)  # New proper date field
    schedule_start_dt = models.DateTimeField(null=True, blank=True)  # New proper datetime field
    schedule_end_dt = models.DateTimeField(null=True, blank=True)  # New proper datetime field
    
    # Content and metadata
    content = models.TextField(blank=True)  # Markdown content
    metadata_json = models.TextField()  # Full metadata as JSON (kept for backward compatibility)
    
    # Extracted metadata fields
    seq_id = models.CharField(max_length=50, blank=True, null=True)  # Sequence ID for ordering
    archived = models.BooleanField(default=False)  # Archive flag
    is_inbox_epic = models.BooleanField(default=False)  # Epic-specific inbox flag
    color = models.CharField(max_length=7, blank=True, null=True)  # Project color (hex)
    notes = models.JSONField(default=list, blank=True)  # Array of note IDs
    dependencies = models.JSONField(default=list, blank=True)  # Array of dependency entity IDs
    checklist = models.JSONField(default=list, blank=True)  # Array of checklist items with id/title/status
    stats = models.JSONField(default=dict, blank=True)  # Complex nested statistics object
    stats_version = models.IntegerField(null=True, blank=True)  # Stats version
    stats_updated = models.DateTimeField(null=True, blank=True)  # Stats update timestamp
    
    class Meta:
        db_table = 'entities'
        indexes = [
            models.Index(fields=['type']),
            models.Index(fields=['status']),
            models.Index(fields=['status_fk']),
            models.Index(fields=['due_date']),
            models.Index(fields=['due_date_dt']),
            models.Index(fields=['project_id']),
            models.Index(fields=['updated']),
            models.Index(fields=['archived']),
            models.Index(fields=['seq_id']),
        ]


class EntityPerson(models.Model):
    """Many-to-many relationship between Entity and Person."""
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name='assigned_people')
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='assigned_entities')
    created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'entity_persons'
        unique_together = [['entity', 'person']]
        indexes = [
            models.Index(fields=['entity']),
            models.Index(fields=['person']),
        ]
    
    def __str__(self):
        return f"{self.entity.title} - {self.person.name}"


class EntityLabel(models.Model):
    """Many-to-many relationship between Entity and Label."""
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name='labels')
    label = models.ForeignKey(Label, on_delete=models.CASCADE, related_name='entities')
    created = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'entity_labels'
        unique_together = [['entity', 'label']]
        indexes = [
            models.Index(fields=['entity']),
            models.Index(fields=['label']),
        ]
    
    def __str__(self):
        return f"{self.entity.title} - {self.label.name}"


class Update(models.Model):
    """Index of updates for activity feeds."""
    id = models.AutoField(primary_key=True)
    entity_id = models.CharField(max_length=50)
    content = models.TextField()
    timestamp = models.CharField(max_length=50)
    
    class Meta:
        db_table = 'updates'
        indexes = [
            models.Index(fields=['entity_id']),
            models.Index(fields=['timestamp']),
        ]


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
                title,
                content,
                updates,
                people,
                labels
            )
        """)


def init_relationships_table():
    """Initialize relationships table if it does not exist."""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='relationships'
        """)
        if cursor.fetchone():
            return
        
        cursor.execute("""
            CREATE TABLE relationships (
                parent_id TEXT,
                child_id TEXT,
                type TEXT,
                PRIMARY KEY (parent_id, child_id),
                FOREIGN KEY (parent_id) REFERENCES entities(id),
                FOREIGN KEY (child_id) REFERENCES entities(id)
            )
        """)
        cursor.execute("CREATE INDEX idx_relationships_parent ON relationships(parent_id)")
        cursor.execute("CREATE INDEX idx_relationships_child ON relationships(child_id)")


def ensure_index_tables():
    """Ensure all index tables exist."""
    init_search_index()
    init_relationships_table()
