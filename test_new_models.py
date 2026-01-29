#!/usr/bin/env python3
"""
Quick test to verify new models are working properly.
"""
import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.models import Project, Epic, Task, Subtask, Note

# Test counts
project_count = Project.objects.count()
epic_count = Epic.objects.count()
task_count = Task.objects.count()
subtask_count = Subtask.objects.count()
note_count = Note.objects.count()

print("✅ Model Query Test Results:")
print(f"   Projects: {project_count}")
print(f"   Epics: {epic_count}")
print(f"   Tasks: {task_count}")
print(f"   Subtasks: {subtask_count}")
print(f"   Notes: {note_count}")

# Test relationships
if project_count > 0:
    project = Project.objects.first()
    print(f"\n✅ Sample Project: {project.title}")
    print(f"   Status: {project.status_fk.display_name}")
    print(f"   Epics: {project.epics.count()}")
    print(f"   Tasks: {project.tasks.count()}")
    print(f"   Subtasks: {project.subtasks.count()}")

if epic_count > 0:
    epic = Epic.objects.first()
    print(f"\n✅ Sample Epic: {epic.title}")
    print(f"   Project: {epic.project.title}")
    print(f"   Tasks: {epic.tasks.count()}")

if task_count > 0:
    task = Task.objects.first()
    print(f"\n✅ Sample Task: {task.title}")
    print(f"   Project: {task.project.title}")
    if task.epic:
        print(f"   Epic: {task.epic.title}")
    print(f"   Subtasks: {task.subtasks.count()}")

print("\n✅ All model queries successful!")
