#!/usr/bin/env python3
"""
Batch update script to replace Entity.objects.filter patterns with specialized model queries.
"""

import re

# Patterns to replace
replacements = [
    # Project queries
    (r"Entity\.objects\.filter\(type='project'\)", "Project.objects.all()"),
    (r"Entity\.objects\.select_related\('status_fk'\)\.filter\(type='project'\)", "Project.objects.select_related('status_fk')"),
    
    # Epic queries  
    (r"Entity\.objects\.filter\(type='epic', project_id=(\w+)\)", r"Epic.objects.filter(project_id=\1)"),
    (r"Entity\.objects\.select_related\('status_fk'\)\.filter\(type='epic', project_id=(\w+)\)", r"Epic.objects.select_related('status_fk', 'project').filter(project_id=\1)"),
    
    # Task queries
    (r"Entity\.objects\.filter\(type='task'\)", "Task.objects.all()"),
    (r"Entity\.objects\.filter\(type='task', project_id=(\w+)\)", r"Task.objects.filter(project_id=\1)"),
    (r"Entity\.objects\.select_related\('status_fk'\)\.filter\(type='task'\)", "Task.objects.select_related('status_fk', 'project', 'epic')"),
    (r"Entity\.objects\.select_related\('status_fk'\)\.filter\(type='task', project_id=(\w+)\)", r"Task.objects.select_related('status_fk', 'project', 'epic').filter(project_id=\1)"),
    
    # Subtask queries
    (r"Entity\.objects\.filter\(type='subtask'\)", "Subtask.objects.all()"),
    (r"Entity\.objects\.filter\(type='subtask', project_id=(\w+)\)", r"Subtask.objects.filter(project_id=\1)"),
    (r"Entity\.objects\.select_related\('status_fk'\)\.filter\(type='subtask', project_id=(\w+), task_id=(\w+)\)", r"Subtask.objects.select_related('status_fk', 'project', 'task', 'epic').filter(project_id=\1, task_id=\2)"),
    
    # Note queries
    (r"Entity\.objects\.filter\(type='note'\)", "Note.objects.all()"),
    
    # Get queries
    (r"Entity\.objects\.get\(id=(\w+), type='project'\)", r"Project.objects.get(id=\1)"),
    (r"Entity\.objects\.select_related\('status_fk'\)\.get\(id=(\w+), type='project'\)", r"Project.objects.select_related('status_fk').get(id=\1)"),
    (r"Entity\.objects\.get\(id=(\w+), type='epic', project_id=(\w+)\)", r"Epic.objects.get(id=\1, project_id=\2)"),
    (r"Entity\.objects\.get\(id=(\w+), type='task', project_id=(\w+)\)", r"Task.objects.get(id=\1, project_id=\2)"),
    (r"Entity\.objects\.get\(id=(\w+), type='subtask', project_id=(\w+), task_id=(\w+)\)", r"Subtask.objects.get(id=\1, project_id=\2, task_id=\3)"),
]

def update_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    
    for pattern, replacement in replacements:
        content = re.sub(pattern, replacement, content)
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ Updated {filepath}")
        return True
    else:
        print(f"  No changes needed in {filepath}")
        return False

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = '/home/jack/scripts/lazy_network_engineer/pm/views.py'
    
    update_file(filepath)
