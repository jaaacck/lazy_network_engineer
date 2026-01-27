"""
Test to verify that posting new updates preserves existing update types.

This test verifies the fix for the bug where posting a new update in the
activity section changes all existing activity types from 'system' to 'user'.
"""
from django.test import TestCase
from pm.models import Entity, Update, ensure_index_tables
from pm.storage.index_storage import IndexStorage
from datetime import datetime


class UpdateTypePreservationTest(TestCase):
    """Test that update types are preserved when posting new updates."""
    
    def setUp(self):
        """Set up test environment."""
        ensure_index_tables()
        self.storage = IndexStorage()
        
    def test_posting_new_update_preserves_existing_types(self):
        """
        Test that when a new update is posted, existing system updates
        remain as 'system' type and don't change to 'user'.
        """
        # Create parent entities first
        project_id = 'project-test1234'
        task_id = 'task-test1234'
        
        # Create project
        self.storage.sync_entity(
            entity_id=project_id,
            entity_type='project',
            metadata={'title': 'Test Project', 'status': 'active'},
            content=''
        )
        
        # Create task
        self.storage.sync_entity(
            entity_id=task_id,
            entity_type='task',
            metadata={
                'title': 'Test Task',
                'status': 'todo',
                'project_id': project_id
            },
            content=''
        )
        
        # Create a test entity (subtask)
        entity_id = 'subtask-test1234'
        metadata = {
            'title': 'Test Subtask',
            'status': 'todo',
            'project_id': project_id,
            'task_id': task_id,
            'updates': [
                {
                    'timestamp': '2024-01-01T10:00:00',
                    'content': 'System created this task',
                    'type': 'system',
                    'activity_type': 'created'
                },
                {
                    'timestamp': '2024-01-02T10:00:00',
                    'content': 'Status changed to in_progress',
                    'type': 'system',
                    'activity_type': 'status_changed'
                }
            ]
        }
        
        # Save entity with initial system updates
        self.storage.sync_entity(
            entity_id=entity_id,
            entity_type='subtask',
            metadata=metadata,
            content='Test content'
        )
        
        # Verify initial updates are saved correctly
        updates = Update.objects.filter(entity_id=entity_id).order_by('timestamp')
        self.assertEqual(updates.count(), 2)
        self.assertEqual(updates[0].type, 'system')
        self.assertEqual(updates[0].activity_type, 'created')
        self.assertEqual(updates[1].type, 'system')
        self.assertEqual(updates[1].activity_type, 'status_changed')
        
        # Now add a new user update (simulating posting a new update)
        metadata['updates'].append({
            'timestamp': '2024-01-03T10:00:00',
            'content': 'User posted an update',
            'type': 'user'
        })
        
        # Save entity again (this simulates what happens when user posts update)
        self.storage.sync_entity(
            entity_id=entity_id,
            entity_type='subtask',
            metadata=metadata,
            content='Test content'
        )
        
        # Verify that existing system updates still have type='system'
        # This is the critical test - the bug would cause these to become 'user'
        updates = Update.objects.filter(entity_id=entity_id).order_by('timestamp')
        self.assertEqual(updates.count(), 3)
        
        # First two updates should still be 'system'
        self.assertEqual(updates[0].type, 'system', 
                        "First system update should remain 'system' type")
        self.assertEqual(updates[0].activity_type, 'created',
                        "First system update should retain activity_type")
        
        self.assertEqual(updates[1].type, 'system',
                        "Second system update should remain 'system' type")
        self.assertEqual(updates[1].activity_type, 'status_changed',
                        "Second system update should retain activity_type")
        
        # Third update should be 'user'
        self.assertEqual(updates[2].type, 'user',
                        "New user update should have 'user' type")
        self.assertIsNone(updates[2].activity_type,
                         "User update should have None activity_type")
    
    def test_editing_update_preserves_type(self):
        """
        Test that editing an existing update's content preserves its type.
        """
        # Create parent entities first
        project_id = 'project-test1234'
        task_id = 'task-test1234'
        
        # Create project
        self.storage.sync_entity(
            entity_id=project_id,
            entity_type='project',
            metadata={'title': 'Test Project', 'status': 'active'},
            content=''
        )
        
        # Create task
        self.storage.sync_entity(
            entity_id=task_id,
            entity_type='task',
            metadata={
                'title': 'Test Task',
                'status': 'todo',
                'project_id': project_id
            },
            content=''
        )
        
        entity_id = 'subtask-test5678'
        metadata = {
            'title': 'Test Subtask 2',
            'status': 'todo',
            'project_id': project_id,
            'task_id': task_id,
            'updates': [
                {
                    'timestamp': '2024-01-01T10:00:00',
                    'content': 'Original system message',
                    'type': 'system',
                    'activity_type': 'created'
                }
            ]
        }
        
        # Save initial entity
        self.storage.sync_entity(
            entity_id=entity_id,
            entity_type='subtask',
            metadata=metadata,
            content='Test content'
        )
        
        # Verify initial state
        update = Update.objects.get(entity_id=entity_id)
        self.assertEqual(update.type, 'system')
        self.assertEqual(update.content, 'Original system message')
        
        # Edit the content (metadata might not have type anymore)
        metadata['updates'][0]['content'] = 'Edited system message'
        # Note: type might be missing from metadata in real scenarios
        metadata['updates'][0].pop('type', None)
        metadata['updates'][0].pop('activity_type', None)
        
        # Re-save
        self.storage.sync_entity(
            entity_id=entity_id,
            entity_type='subtask',
            metadata=metadata,
            content='Test content'
        )
        
        # Verify type is preserved even when not in metadata
        update = Update.objects.get(entity_id=entity_id)
        self.assertEqual(update.type, 'system',
                        "Update type should be preserved even when missing from metadata")
        self.assertEqual(update.activity_type, 'created',
                        "Activity type should be preserved even when missing from metadata")
        self.assertEqual(update.content, 'Edited system message',
                        "Content should be updated")


if __name__ == '__main__':
    import django
    from django.conf import settings
    
    # Configure minimal Django settings for testing
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            },
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.auth',
                'pm',
            ],
        )
        django.setup()
    
    import unittest
    unittest.main()
