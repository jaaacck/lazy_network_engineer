from django.db import models
from django.db import connection
import json
import os
from django.conf import settings


class Entity(models.Model):
    """Index of all entities (projects, epics, tasks, subtasks, notes)."""
    id = models.CharField(max_length=50, primary_key=True)
    type = models.CharField(max_length=20)  # 'project', 'epic', 'task', 'subtask', 'note'
    title = models.CharField(max_length=500)
    status = models.CharField(max_length=50, blank=True)
    priority = models.IntegerField(null=True, blank=True)
    created = models.CharField(max_length=50, blank=True)
    updated = models.CharField(max_length=50, blank=True)
    
    # Relationships
    project_id = models.CharField(max_length=50, null=True, blank=True)
    epic_id = models.CharField(max_length=50, null=True, blank=True)
    task_id = models.CharField(max_length=50, null=True, blank=True)
    
    # Scheduling
    due_date = models.CharField(max_length=50, blank=True)
    schedule_start = models.CharField(max_length=50, blank=True)
    schedule_end = models.CharField(max_length=50, blank=True)
    
    # File tracking
    file_path = models.CharField(max_length=500, unique=True)
    file_mtime = models.FloatField()  # Modification time
    metadata_json = models.TextField()  # Full metadata as JSON
    
    class Meta:
        db_table = 'entities'
        indexes = [
            models.Index(fields=['type']),
            models.Index(fields=['status']),
            models.Index(fields=['due_date']),
            models.Index(fields=['project_id']),
            models.Index(fields=['updated']),
        ]


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
