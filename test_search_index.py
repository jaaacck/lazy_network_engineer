#!/usr/bin/env python
"""Test script to debug search index issues."""
import os
import django
import traceback

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.storage.index_storage import IndexStorage
from pm.models import Project

# Test 1: Try updating search index directly
print("Test 1: Direct _update_search_index call")
try:
    index_storage = IndexStorage()
    index_storage._update_search_index(
        "test-123",
        "project",
        "Test Title",
        "Test Content",
        "Some updates text",
        ["person1", "person2"],
        ["label1", "label2"]
    )
    print("  ✓ Success!")
except Exception as e:
    print(f"  ✗ Error: {e}")
    traceback.print_exc()

# Test 2: Try syncing a real project
print("\nTest 2: Syncing a real project")
try:
    project = Project.objects.first()
    if project:
        from pm.views import _build_metadata_from_entity
        metadata = _build_metadata_from_entity(project)
        
        updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
        people_tags = metadata.get('people', [])
        labels = metadata.get('labels', [])
        
        print(f"  Project: {project.id}")
        print(f"  People tags type: {type(people_tags)}, value: {people_tags}")
        print(f"  Labels type: {type(labels)}, value: {labels}")
        
        index_storage._update_search_index(
            project.id,
            'project',
            project.title or '',
            project.content or '',
            updates_text,
            people_tags,
            labels
        )
        print("  ✓ Success!")
    else:
        print("  No projects found")
except Exception as e:
    print(f"  ✗ Error: {e}")
    traceback.print_exc()
