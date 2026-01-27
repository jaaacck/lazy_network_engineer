#!/usr/bin/env python3
"""
Script to migrate views.py from status/status_fk to only status_fk
"""
import re

with open('pm/views.py', 'r') as f:
    content = f.read()

# 1. Remove the _status_display_fallback function
fallback_pattern = r'def _status_display_fallback\(status_name\):.*?return s\.display_name if s else \(status_name or \'\'\)\.replace\(\'_\', \' \'\)\.title\(\)\n\n'
content = re.sub(fallback_pattern, '', content, flags=re.DOTALL)

# 2. Add helper functions after _merge_people_from_entityperson
merge_people_end = content.find('def _build_metadata_from_entity(entity):')
helper_functions = '''

def get_status_display(entity):
    """Get display name for entity status.
    
    Always use status_fk (foreign key to Status table).
    """
    if entity.status_fk:
        return entity.status_fk.display_name
    # Should not happen with NOT NULL constraint
    logger.error(f"Entity {entity.id} has no status_fk set")
    return 'Unknown'


def get_status_for_entity_type(entity_type):
    """Get all active statuses for a given entity type.
    
    Returns a list of Status objects applicable to this entity type.
    Used for populating status dropdowns in templates.
    """
    from django.db.models import Q
    statuses = Status.objects.filter(
        is_active=True
    ).filter(
        Q(entity_types__contains=entity_type) | Q(entity_types__contains='all')
    ).order_by('order', 'name')
    return statuses


'''

if helper_functions.strip() not in content:
    content = content[:merge_people_end] + helper_functions + content[merge_people_end:]

# 3. Update _build_metadata_from_entity to use status_fk.name
old_status_line = r"'status': entity\.status or '',"
new_status_line = "'status': entity.status_fk.name if entity.status_fk else '',"
content = re.sub(old_status_line, new_status_line, content)

# 4. Replace all status_display = ... patterns
# Pattern 1: metadata['status_display'] = entity.status_fk...
pattern1 = r"metadata\['status_display'\] = entity\.status_fk\.display_name if entity\.status_fk else _status_display_fallback\([^)]*\)"
replacement1 = "metadata['status_display'] = get_status_display(entity)"
content = re.sub(pattern1, replacement1, content)

# Pattern 2: 'status_display': entity.status_fk...
pattern2 = r"'status_display': entity\.status_fk\.display_name if entity\.status_fk else _status_display_fallback\([^)]*\)"
replacement2 = "'status_display': get_status_display(entity)"
content = re.sub(pattern2, replacement2, content)

# Pattern 3: 'status_display': task_entity.status_fk...
pattern3 = r"'status_display': task_entity\.status_fk\.display_name if task_entity\.status_fk else _status_display_fallback\([^)]*\)"
replacement3 = "'status_display': get_status_display(task_entity)"
content = re.sub(pattern3, replacement3, content)

# Pattern 4: 'status_display': epic_entity.status_fk...
pattern4 = r"'status_display': epic_entity\.status_fk\.display_name if epic_entity\.status_fk else _status_display_fallback\([^)]*\)"
replacement4 = "'status_display': get_status_display(epic_entity)"
content = re.sub(pattern4, replacement4, content)

# Pattern 5: 'status_display': subtask_entity.status_fk...
pattern5 = r"'status_display': subtask_entity\.status_fk\.display_name if subtask_entity\.status_fk else _status_display_fallback\([^)]*\)"
replacement5 = "'status_display': get_status_display(subtask_entity)"
content = re.sub(pattern5, replacement5, content)

# Pattern 6: 'status_display': task.status_fk...
pattern6 = r"'status_display': task\.status_fk\.display_name if task\.status_fk else _status_display_fallback\([^)]*\)"
replacement6 = "'status_display': get_status_display(task)"
content = re.sub(pattern6, replacement6, content)

# Pattern 7: 'status_display': subtask.status_fk...
pattern7 = r"'status_display': subtask\.status_fk\.display_name if subtask\.status_fk else _status_display_fallback\([^)]*\)"
replacement7 = "'status_display': get_status_display(subtask)"
content = re.sub(pattern7, replacement7, content)

# 5. Fix load/save function return statements
# Fix load_epic return
load_epic_old = r"return\s*\n    except Entity\.DoesNotExist:\s*\n        return\s*\n\s*\ndef save_epic"
load_epic_new = "return metadata, content\n    except Entity.DoesNotExist:\n        return None, None\n\n\ndef save_epic"
content = re.sub(load_epic_old, load_epic_new, content, flags=re.DOTALL)

# Fix load_task return  
load_task_old = r"return\s*\n    except Entity\.DoesNotExist:\s*\n        return None, None\s*\n\s*\ndef save_task"
load_task_new = "return metadata, content\n    except Entity.DoesNotExist:\n        return None, None\n\n\ndef save_task"
content = re.sub(load_task_old, load_task_new, content, flags=re.DOTALL)

# Fix load_subtask return
load_subtask_old = r"return\s*\n    except Entity\.DoesNotExist:\s*\n        return\s*\n\s*\ndef save_subtask"
load_subtask_new = "return metadata, content\n    except Entity.DoesNotExist:\n        return None, None\n\n\ndef save_subtask"
content = re.sub(load_subtask_old, load_subtask_new, content, flags=re.DOTALL)

# 6. Update compute_project_stats to use status_fk
old_done_tasks = r"done_tasks_count = tasks\.filter\(status='done'\)\.count\(\)"
new_done_tasks = "done_tasks_count = tasks.filter(status_fk__name='done').count()"
content = re.sub(old_done_tasks, new_done_tasks, content)

old_done_subtasks = r"done_subtasks_count = subtasks\.filter\(status='done'\)\.count\(\)"
new_done_subtasks = "done_subtasks_count = subtasks.filter(status_fk__name='done').count()"
content = re.sub(old_done_subtasks, new_done_subtasks, content)

# 7. Update project_list status handling
old_project_status = r"status_name = entity\.status or 'active'"
new_project_status = "status_name = entity.status_fk.name if entity.status_fk else 'active'"
content = re.sub(old_project_status, new_project_status, content)

old_status_display_in_project_list = r"'status_display': entity\.status_fk\.display_name if entity\.status_fk else _status_display_fallback\(status_name or entity\.status\),"
new_status_display = "'status_display': get_status_display(entity),"
content = re.sub(old_status_display_in_project_list, new_status_display, content)

with open('pm/views.py', 'w') as f:
    f.write(content)

print("Migration completed successfully")
