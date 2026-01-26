#!/usr/bin/env python
"""Test that templates can be loaded and rendered."""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from django.template.loader import get_template

def test_templates():
    """Test that all main templates load correctly."""
    templates = [
        'pm/project_list.html',
        'pm/project_detail.html',
        'pm/epic_detail.html',
        'pm/task_detail.html',
        'pm/subtask_detail.html',
        'pm/new_project.html',
        'pm/new_epic.html',
        'pm/new_task.html',
        'pm/new_subtask.html',
    ]

    print("Testing template loading...")
    for template_name in templates:
        try:
            template = get_template(template_name)
            print(f"✓ {template_name}")
        except Exception as e:
            print(f"✗ {template_name}: {e}")
            return False

    print("\n✓ All templates loaded successfully!")
    return True

if __name__ == '__main__':
    success = test_templates()
    sys.exit(0 if success else 1)
