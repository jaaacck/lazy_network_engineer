from django.test import TestCase, Client
from django.urls import reverse
from pm.views import (
    validate_id, safe_join_path, is_valid_project_id,
    INBOX_PROJECT_ID, ensure_inbox_project, get_inbox_epic
)
from pm.utils import load_entity, save_entity
from pm.models import ensure_index_tables
import os
from django.conf import settings


class BasicFunctionalityTests(TestCase):
    """Basic tests for core functionality."""
    
    def setUp(self):
        """Set up test client and initialize database tables."""
        self.client = Client()
        # Ensure index tables exist for tests
        ensure_index_tables()
    
    def test_project_list_loads(self):
        """Test that project list page loads."""
        response = self.client.get(reverse('project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Projects')
    
    def test_inbox_view_redirects(self):
        """Test that inbox view redirects to epic."""
        ensure_inbox_project()
        response = self.client.get(reverse('inbox'))
        self.assertEqual(response.status_code, 302)  # Redirect
    
    def test_search_page_loads(self):
        """Test that search page loads."""
        response = self.client.get(reverse('search'))
        self.assertEqual(response.status_code, 200)
    
    def test_my_work_loads(self):
        """Test that my work page loads."""
        response = self.client.get(reverse('my_work'))
        self.assertEqual(response.status_code, 200)
    
    def test_today_loads(self):
        """Test that today page loads."""
        response = self.client.get(reverse('today'))
        self.assertEqual(response.status_code, 200)
    
    def test_notes_list_loads(self):
        """Test that notes list page loads."""
        response = self.client.get(reverse('notes_list'))
        self.assertEqual(response.status_code, 200)
    
    def test_people_list_loads(self):
        """Test that people list page loads."""
        response = self.client.get(reverse('people_list'))
        self.assertEqual(response.status_code, 200)


class ValidationTests(TestCase):
    """Tests for validation functions."""
    
    def test_validate_id_valid_project(self):
        """Test valid project ID validation."""
        from pm.utils import validate_id
        self.assertTrue(validate_id('project-12345678', 'project'))
        self.assertFalse(validate_id('project-123', 'project'))  # Too short
        self.assertFalse(validate_id('invalid', 'project'))
    
    def test_is_valid_project_id_inbox(self):
        """Test that inbox project ID is valid."""
        self.assertTrue(is_valid_project_id(INBOX_PROJECT_ID))
        self.assertTrue(is_valid_project_id('project-12345678'))
        self.assertFalse(is_valid_project_id('invalid'))
    
    def test_safe_join_path(self):
        """Test safe path joining prevents directory traversal."""
        base = settings.DATA_ROOT
        # Valid paths
        result = safe_join_path('projects', 'project-12345678')
        self.assertTrue(result.startswith(base))
        
        # Path traversal attempts should be blocked (raises Http404)
        from django.http import Http404
        with self.assertRaises(Http404):
            safe_join_path('projects', '../../../etc/passwd')


class InboxTests(TestCase):
    """Tests for inbox functionality."""
    
    def test_ensure_inbox_project_creates_structure(self):
        """Test that inbox project structure is created."""
        ensure_inbox_project()
        inbox_path = safe_join_path('projects', f'{INBOX_PROJECT_ID}.md')
        self.assertTrue(os.path.exists(inbox_path))
        
        # Check that inbox epic exists
        epic_id = get_inbox_epic()
        self.assertIsNotNone(epic_id)
        epic_path = safe_join_path('projects', INBOX_PROJECT_ID, 'epics', f'{epic_id}.md')
        self.assertTrue(os.path.exists(epic_path))


class URLTests(TestCase):
    """Tests for URL routing."""
    
    def setUp(self):
        self.client = Client()
    
    def test_all_main_urls_exist(self):
        """Test that all main URLs are defined."""
        urls = [
            'project_list',
            'inbox',
            'my_work',
            'today',
            'calendar',
            'notes_list',
            'people_list',
            'search',
        ]
        for url_name in urls:
            try:
                url = reverse(url_name)
                self.assertIsNotNone(url)
            except Exception as e:
                self.fail(f"URL '{url_name}' not found: {e}")
