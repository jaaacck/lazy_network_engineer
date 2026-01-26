import os
import logging
import hashlib
import re
import shutil
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta
import uuid
from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import JsonResponse, Http404
from django.core.cache import cache
from django.utils.html import escape
from django.utils.safestring import mark_safe
from .utils import (
    load_entity, save_entity, validate_id, safe_join_path, 
    calculate_markdown_progress, calculate_checklist_progress
)
from .storage import SyncManager

# Initialize sync manager
sync_manager = SyncManager()

logger = logging.getLogger('pm')
STATS_VERSION = 1

# Color palette for projects (distinct colors)
PROJECT_COLORS = [
    '#ff6600', '#0066ff', '#00cc66', '#cc00ff', '#ff0066',
    '#00ffcc', '#ffcc00', '#6600ff', '#ff3300', '#00ff33',
    '#ff0099', '#0099ff', '#99ff00', '#ff9900', '#9900ff'
]

def get_project_color(project_id, existing_color=None):
    """Generate or return project color."""
    if existing_color:
        return existing_color
    # Generate color based on project ID hash
    hash_obj = hashlib.md5(project_id.encode())
    hash_int = int(hash_obj.hexdigest(), 16)
    return PROJECT_COLORS[hash_int % len(PROJECT_COLORS)]

def hex_to_rgba(hex_color, alpha=0.1):
    """Convert hex color to rgba string."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f'rgba({r}, {g}, {b}, {alpha})'

LABEL_COLORS = [
    '#ff6600', '#0066ff', '#00cc66', '#cc00ff', '#ff0066',
    '#00ffcc', '#ffcc00', '#6600ff', '#ff3300', '#00ff33'
]

def label_color(label):
    """Deterministic label color."""
    hash_obj = hashlib.md5(label.encode())
    idx = int(hash_obj.hexdigest(), 16) % len(LABEL_COLORS)
    return LABEL_COLORS[idx]

def normalize_labels(raw):
    """Normalize labels from string or list to list of strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in str(raw).split(',') if s.strip()]

def normalize_people(raw):
    """Normalize people tags from string or list to list of strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip().lstrip('@') for x in raw if str(x).strip()]
    return [s.strip().lstrip('@') for s in str(raw).split(',') if s.strip()]


def add_activity_entry(metadata, activity_type, old_value=None, new_value=None, details=None):
    """Add a system activity entry to metadata.
    
    Args:
        metadata: The entity metadata dict
        activity_type: Type of activity (e.g., 'status_changed', 'priority_changed', 'label_added')
        old_value: Previous value (optional)
        new_value: New value (optional)
        details: Additional details dict (optional)
    """
    if 'updates' not in metadata:
        metadata['updates'] = []
    
    # Build activity message
    message_parts = []
    if activity_type == 'status_changed':
        message_parts.append(f"Status changed from {old_value} to {new_value}")
    elif activity_type == 'priority_changed':
        if old_value:
            message_parts.append(f"Priority changed from P{old_value} to P{new_value}" if new_value else f"Priority removed (was P{old_value})")
        else:
            message_parts.append(f"Priority set to P{new_value}")
    elif activity_type == 'schedule_start_changed':
        message_parts.append(f"Start time set to {new_value}" if new_value else "Start time removed")
    elif activity_type == 'schedule_end_changed':
        message_parts.append(f"End time set to {new_value}" if new_value else "End time removed")
    elif activity_type == 'due_date_changed':
        message_parts.append(f"Due date set to {new_value}" if new_value else "Due date removed")
    elif activity_type == 'label_added':
        message_parts.append(f"Label '{new_value}' added")
    elif activity_type == 'label_removed':
        message_parts.append(f"Label '{old_value}' removed")
    elif activity_type == 'person_added':
        message_parts.append(f"Person '{new_value}' added")
    elif activity_type == 'person_removed':
        message_parts.append(f"Person '{old_value}' removed")
    elif activity_type == 'note_linked':
        message_parts.append(f"Note '{new_value}' linked")
    elif activity_type == 'note_unlinked':
        message_parts.append(f"Note '{old_value}' unlinked")
    elif activity_type == 'dependency_added':
        message_parts.append(f"Dependency '{new_value}' added")
    elif activity_type == 'dependency_removed':
        message_parts.append(f"Dependency '{old_value}' removed")
    elif activity_type == 'created':
        message_parts.append("Created")
    else:
        message_parts.append(f"{activity_type}: {new_value or old_value}")
    
    activity_entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'content': ' '.join(message_parts),
        'type': 'system',
        'activity_type': activity_type
    }
    
    if details:
        activity_entry['details'] = details
    
    metadata['updates'].append(activity_entry)


def get_all_labels_in_system():
    """Get all unique labels used across epics, tasks, subtasks, and notes."""
    cache_key = "all_labels:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    labels = set()
    
    # Scan projects for epics, tasks and subtasks
    projects_dir = safe_join_path('projects')
    if os.path.exists(projects_dir):
        try:
            for p_id in os.listdir(projects_dir):
                p_dir = os.path.join(projects_dir, p_id)
                if not os.path.isdir(p_dir):
                    continue
                epics_dir = os.path.join(p_dir, 'epics')
                if not os.path.exists(epics_dir):
                    continue
                for e_file in os.listdir(epics_dir):
                    if not e_file.endswith('.md'):
                        continue
                    epic_id = e_file[:-3]
                    # Check epic labels
                    e_meta, _ = load_epic(p_id, epic_id, metadata_only=True)
                    if e_meta:
                        for lbl in normalize_labels(e_meta.get('labels', [])):
                            labels.add(lbl)
                    
                    tasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks')
                    if not os.path.exists(tasks_dir):
                        continue
                    for t_file in os.listdir(tasks_dir):
                        if not t_file.endswith('.md'):
                            continue
                        task_id = t_file[:-3]
                        t_meta, _ = load_task(p_id, epic_id, task_id, metadata_only=True)
                        if t_meta:
                            for lbl in normalize_labels(t_meta.get('labels', [])):
                                labels.add(lbl)
                        # Check subtasks
                        subtasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                        if os.path.exists(subtasks_dir):
                            for s_file in os.listdir(subtasks_dir):
                                if not s_file.endswith('.md'):
                                    continue
                                subtask_id = s_file[:-3]
                                s_meta, _ = load_subtask(p_id, epic_id, task_id, subtask_id, metadata_only=True)
                                if s_meta:
                                    for lbl in normalize_labels(s_meta.get('labels', [])):
                                        labels.add(lbl)
        except OSError:
            pass
    
    # Scan notes
    notes_dir = safe_join_path('notes')
    if os.path.exists(notes_dir):
        try:
            for n_file in os.listdir(notes_dir):
                if not n_file.endswith('.md'):
                    continue
                note_id = n_file[:-3]
                n_meta, _ = load_note(note_id, metadata_only=True)  # Only need metadata for labels
                if n_meta:
                    for lbl in normalize_labels(n_meta.get('labels', [])):
                        labels.add(lbl)
        except OSError:
            pass
    
    result = sorted(labels, key=str.lower)
    cache.set(cache_key, result, 300)  # Cache for 5 minutes instead of 60 seconds
    return result


def find_person_by_name(person_name):
    """Find person_id by name. Returns None if not found."""
    person_normalized = person_name.strip().lstrip('@')
    if not person_normalized:
        return None
    
    # Cache lookup by name
    cache_key = f"person_by_name:{person_normalized.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    # Build name-to-ID mapping if not cached
    name_to_id_cache_key = "person_name_to_id_map:v1"
    name_to_id_map = cache.get(name_to_id_cache_key)
    
    if name_to_id_map is None:
        # Build the mapping by scanning people directory once
        name_to_id_map = {}
        people_dir = safe_join_path('people')
        if os.path.exists(people_dir):
            try:
                for p_file in os.listdir(people_dir):
                    if not p_file.endswith('.md'):
                        continue
                    person_id = p_file[:-3]
                    # Validate it is a person ID
                    if not validate_id(person_id, 'person'):
                        continue
                    p_meta, _ = load_person(person_id, metadata_only=True)
                    if p_meta:
                        person_name_from_file = p_meta.get('name', '').strip().lstrip('@')
                        if person_name_from_file:
                            name_to_id_map[person_name_from_file.lower()] = person_id
            except OSError:
                pass
        
        # Cache the mapping for 5 minutes
        cache.set(name_to_id_cache_key, name_to_id_map, 300)
    
    # Look up in the mapping
    person_id = name_to_id_map.get(person_normalized.lower())
    
    # Cache individual lookups for 5 minutes
    if person_id:
        cache.set(cache_key, person_id, 300)
    
    return person_id


def load_person(person_id, metadata_only=False):
    """Load a person from disk by person_id."""
    if not validate_id(person_id, 'person'):
        return None, None
    person_path = safe_join_path('people', f'{person_id}.md')
    if not os.path.exists(person_path):
        return None, None
    return sync_manager.load_entity_with_index(
        person_path, person_id, 'person',
        'Untitled Person', 'active', metadata_only=metadata_only
    )


def save_person(person_id, metadata, content=''):
    """Save a person to disk by person_id."""
    if not validate_id(person_id, 'person'):
        raise Http404("Invalid person ID")
    person_path = safe_join_path('people', f'{person_id}.md')
    os.makedirs(os.path.dirname(person_path), exist_ok=True)
    
    # Ensure person_id is in metadata
    metadata['id'] = person_id
    
    sync_manager.save_entity_with_sync(person_path, person_id, 'person', metadata, content)


def get_all_people_names_in_system():
    """Get all unique people names for dropdowns.
    Returns a list of person names.
    Optimized to avoid double-scanning by reusing get_all_people_in_system results."""
    cache_key = "all_people_names:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    people_names = set()
    
    # Get all person IDs and extract names (this is already cached)
    people_ids = get_all_people_in_system()
    for person_id in people_ids:
        person_meta, _ = load_person(person_id, metadata_only=True)
        if person_meta:
            person_name = person_meta.get('name', '').strip()
            # Skip if name is empty, looks like an ID (15 chars), or equals the ID
            if person_name and not (person_name.startswith('person-') and len(person_name) == 15) and person_name != person_id:
                people_names.add(person_name)
    
    # Note: We do not need to scan entities again since get_all_people_in_system already does that
    # and we have extracted names from all person IDs. Any person referenced in entities should
    # already be in people_ids if they have a file, or will be a name that we cannot resolve anyway.
    
    result = sorted(people_names, key=str.lower)
    cache.set(cache_key, result, 300)  # Cache for 5 minutes instead of 60 seconds
    return result


def get_all_people_in_system():
    """Get all unique people - from people directory and from entity metadata.
    Returns a list of person IDs."""
    cache_key = "all_people:v3"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    people_ids = set()
    
    # First, scan people directory for standalone people
    people_dir = safe_join_path('people')
    if os.path.exists(people_dir):
        try:
            for p_file in os.listdir(people_dir):
                if not p_file.endswith('.md'):
                    continue
                person_id = p_file[:-3]
                if validate_id(person_id, 'person'):
                    people_ids.add(person_id)
        except OSError:
            pass
    
    # Then scan projects for epics, tasks and subtasks
    projects_dir = safe_join_path('projects')
    if os.path.exists(projects_dir):
        try:
            for p_id in os.listdir(projects_dir):
                p_dir = os.path.join(projects_dir, p_id)
                if not os.path.isdir(p_dir):
                    continue
                # Check project people
                p_meta, _ = load_project(p_id, metadata_only=True)
                if p_meta:
                    for person_name in normalize_people(p_meta.get('people', [])):
                        person_id = find_person_by_name(person_name)
                        if person_id:
                            people_ids.add(person_id)
                
                epics_dir = os.path.join(p_dir, 'epics')
                if not os.path.exists(epics_dir):
                    continue
                for e_file in os.listdir(epics_dir):
                    if not e_file.endswith('.md'):
                        continue
                    epic_id = e_file[:-3]
                    # Check epic people
                    e_meta, _ = load_epic(p_id, epic_id, metadata_only=True)
                    if e_meta:
                        for person_name in normalize_people(e_meta.get('people', [])):
                            person_id = find_person_by_name(person_name)
                            if person_id:
                                people_ids.add(person_id)
                    
                    tasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks')
                    if not os.path.exists(tasks_dir):
                        continue
                    for t_file in os.listdir(tasks_dir):
                        if not t_file.endswith('.md'):
                            continue
                        task_id = t_file[:-3]
                        t_meta, _ = load_task(p_id, epic_id, task_id, metadata_only=True)
                        if t_meta:
                            for person_name in normalize_people(t_meta.get('people', [])):
                                person_id = find_person_by_name(person_name)
                                if person_id:
                                    people_ids.add(person_id)
                        # Check subtasks
                        subtasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                        if os.path.exists(subtasks_dir):
                            for s_file in os.listdir(subtasks_dir):
                                if not s_file.endswith('.md'):
                                    continue
                                subtask_id = s_file[:-3]
                                s_meta, _ = load_subtask(p_id, epic_id, task_id, subtask_id, metadata_only=True)
                                if s_meta:
                                    for person_name in normalize_people(s_meta.get('people', [])):
                                        person_id = find_person_by_name(person_name)
                                        if person_id:
                                            people_ids.add(person_id)
        except OSError:
            pass
    
    # Scan notes
    notes_dir = safe_join_path('notes')
    if os.path.exists(notes_dir):
        try:
            for n_file in os.listdir(notes_dir):
                if not n_file.endswith('.md'):
                    continue
                note_id = n_file[:-3]
                n_meta, _ = load_note(note_id, metadata_only=True)  # Only need metadata
                if n_meta:
                    for person_ref in normalize_people(n_meta.get('people', [])):
                        # Check if it is already a person ID
                        if person_ref.startswith('person-') and len(person_ref) == 15 and validate_id(person_ref, 'person'):
                            people_ids.add(person_ref)
                        else:
                            person_id = find_person_by_name(person_ref)
                            if person_id:
                                people_ids.add(person_id)
        except OSError:
            pass
    
    result = sorted(people_ids)
    cache.set(cache_key, result, 300)  # Cache for 5 minutes instead of 60 seconds
    return result


def get_all_notes_in_system():
    """Get all notes with basic info for dropdown selection."""
    cache_key = "all_notes:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    notes = []
    notes_dir = safe_join_path('notes')
    if os.path.exists(notes_dir):
        try:
            for n_file in os.listdir(notes_dir):
                if not n_file.endswith('.md'):
                    continue
                note_id = n_file[:-3]
                n_meta, _ = load_note(note_id, metadata_only=True)  # Only need metadata, not full content
                if n_meta:
                    notes.append({
                        'id': note_id,
                        'title': n_meta.get('title', 'Untitled Note'),
                        'created': n_meta.get('created', '')
                    })
        except OSError:
            pass
    
    # Sort by title
    result = sorted(notes, key=lambda x: x['title'].lower())
    cache.set(cache_key, result, 300)  # Cache for 5 minutes instead of 60 seconds
    return result


def get_all_entities_for_linking():
    """Get all projects, epics, tasks, and subtasks for linking to notes."""
    cache_key = "all_entities_for_linking:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    entities = {
        'projects': [],
        'epics': [],
        'tasks': [],
        'subtasks': []
    }
    
    projects_dir = safe_join_path('projects')
    if os.path.exists(projects_dir):
        try:
            for p_id in os.listdir(projects_dir):
                p_dir = os.path.join(projects_dir, p_id)
                if not os.path.isdir(p_dir):
                    continue
                
                # Load project
                p_meta, _ = load_project(p_id, metadata_only=True)
                if p_meta:
                    entities['projects'].append({
                        'id': p_id,
                        'title': p_meta.get('title', 'Untitled Project'),
                        'seq_id': ''
                    })
                
                # Load epics
                epics_dir = safe_join_path('projects', p_id, 'epics')
                if os.path.exists(epics_dir):
                    for e_file in os.listdir(epics_dir):
                        if not e_file.endswith('.md'):
                            continue
                        epic_id = e_file[:-3]
                        e_meta, _ = load_epic(p_id, epic_id, metadata_only=True)
                        if e_meta:
                            entities['epics'].append({
                                'id': epic_id,
                                'project_id': p_id,
                                'title': e_meta.get('title', 'Untitled Epic'),
                                'seq_id': e_meta.get('seq_id', '')
                            })
                        
                        # Load tasks
                        tasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks')
                        if os.path.exists(tasks_dir):
                            for t_file in os.listdir(tasks_dir):
                                if not t_file.endswith('.md'):
                                    continue
                                task_id = t_file[:-3]
                                t_meta, _ = load_task(p_id, epic_id, task_id, metadata_only=True)
                                if t_meta:
                                    entities['tasks'].append({
                                        'id': task_id,
                                        'project_id': p_id,
                                        'epic_id': epic_id,
                                        'title': t_meta.get('title', 'Untitled Task'),
                                        'seq_id': t_meta.get('seq_id', '')
                                    })
                                    
                                    # Load subtasks
                                    subtasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                                    if os.path.exists(subtasks_dir):
                                        for s_file in os.listdir(subtasks_dir):
                                            if not s_file.endswith('.md'):
                                                continue
                                            subtask_id = s_file[:-3]
                                            s_meta, _ = load_subtask(p_id, epic_id, task_id, subtask_id, metadata_only=True)
                                            if s_meta:
                                                entities['subtasks'].append({
                                                    'id': subtask_id,
                                                    'project_id': p_id,
                                                    'epic_id': epic_id,
                                                    'task_id': task_id,
                                                    'title': s_meta.get('title', 'Untitled Subtask'),
                                                    'seq_id': s_meta.get('seq_id', '')
                                                })
        except OSError:
            pass
    
    # Sort all by title
    for key in entities:
        entities[key] = sorted(entities[key], key=lambda x: x['title'].lower())
    
    cache.set(cache_key, entities, 300)  # Cache for 5 minutes instead of 60 seconds
    return entities


def find_note_backlinks(note_id):
    """Find all entities (projects, epics, tasks, subtasks) that have linked this note."""
    backlinks = {
        'projects': [],
        'epics': [],
        'tasks': [],
        'subtasks': []
    }
    
    projects_dir = safe_join_path('projects')
    if not os.path.exists(projects_dir):
        return backlinks
    
    try:
        for p_id in os.listdir(projects_dir):
            p_dir = os.path.join(projects_dir, p_id)
            if not os.path.isdir(p_dir):
                continue
            
            # Check project
            p_meta, _ = load_project(p_id, metadata_only=True)
            if p_meta and note_id in p_meta.get('notes', []):
                backlinks['projects'].append({
                    'id': p_id,
                    'title': p_meta.get('title', 'Untitled Project')
                })
            
            # Check epics
            epics_dir = safe_join_path('projects', p_id, 'epics')
            if os.path.exists(epics_dir):
                for e_file in os.listdir(epics_dir):
                    if not e_file.endswith('.md'):
                        continue
                    epic_id = e_file[:-3]
                    e_meta, _ = load_epic(p_id, epic_id, metadata_only=True)
                    if e_meta and note_id in e_meta.get('notes', []):
                        backlinks['epics'].append({
                            'id': epic_id,
                            'project_id': p_id,
                            'title': e_meta.get('title', 'Untitled Epic'),
                            'seq_id': e_meta.get('seq_id', '')
                        })
                    
                    # Check tasks
                    tasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks')
                    if os.path.exists(tasks_dir):
                        for t_file in os.listdir(tasks_dir):
                            if not t_file.endswith('.md'):
                                continue
                            task_id = t_file[:-3]
                            t_meta, _ = load_task(p_id, epic_id, task_id, metadata_only=True)
                            if t_meta and note_id in t_meta.get('notes', []):
                                backlinks['tasks'].append({
                                    'id': task_id,
                                    'project_id': p_id,
                                    'epic_id': epic_id,
                                    'title': t_meta.get('title', 'Untitled Task'),
                                    'seq_id': t_meta.get('seq_id', '')
                                })
                            
                            # Check subtasks
                            subtasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                            if os.path.exists(subtasks_dir):
                                for s_file in os.listdir(subtasks_dir):
                                    if not s_file.endswith('.md'):
                                        continue
                                    subtask_id = s_file[:-3]
                                    s_meta, _ = load_subtask(p_id, epic_id, task_id, subtask_id, metadata_only=True)
                                    if s_meta and note_id in s_meta.get('notes', []):
                                        backlinks['subtasks'].append({
                                            'id': subtask_id,
                                            'project_id': p_id,
                                            'epic_id': epic_id,
                                            'task_id': task_id,
                                            'title': s_meta.get('title', 'Untitled Subtask'),
                                            'seq_id': s_meta.get('seq_id', '')
                                        })
    except OSError:
        pass
    
    return backlinks


def get_next_seq_id(project_id, entity_type):
    """Get the next sequential ID for epics, tasks, or subtasks within a project.
    
    entity_type: 'epic', 'task', or 'subtask'
    Returns: 'e1', 'e2', etc. for epics; 't1', 't2', etc. for tasks; 'st1', 'st2', etc. for subtasks
    """
    if entity_type == 'epic':
        prefix = 'e'
    elif entity_type == 'task':
        prefix = 't'
    else:  # subtask
        prefix = 'st'
    
    max_seq = 0
    
    if entity_type == 'epic':
        # Scan all epics in the project
        epics_dir = safe_join_path('projects', project_id, 'epics')
        if os.path.exists(epics_dir):
            try:
                for filename in os.listdir(epics_dir):
                    if filename.endswith('.md'):
                        epic_id = filename[:-3]
                        epic_metadata, _ = load_epic(project_id, epic_id, metadata_only=True)
                        if epic_metadata:
                            seq = epic_metadata.get('seq_id', '')
                            if seq and seq.startswith('e'):
                                try:
                                    num = int(seq[1:])
                                    max_seq = max(max_seq, num)
                                except ValueError:
                                    pass
            except OSError:
                pass
    elif entity_type == 'task':
        # Scan all tasks across all epics in the project
        epics_dir = safe_join_path('projects', project_id, 'epics')
        if os.path.exists(epics_dir):
            try:
                for epic_folder in os.listdir(epics_dir):
                    epic_path = os.path.join(epics_dir, epic_folder)
                    if os.path.isdir(epic_path):
                        tasks_dir = os.path.join(epic_path, 'tasks')
                        if os.path.exists(tasks_dir):
                            for filename in os.listdir(tasks_dir):
                                if filename.endswith('.md'):
                                    task_id = filename[:-3]
                                    task_metadata, _ = load_task(project_id, epic_folder, task_id, metadata_only=True)
                                    if task_metadata:
                                        seq = task_metadata.get('seq_id', '')
                                        if seq and seq.startswith('t'):
                                            try:
                                                num = int(seq[1:])
                                                max_seq = max(max_seq, num)
                                            except ValueError:
                                                pass
            except OSError:
                pass
    else:  # subtask
        # Scan all subtasks across all tasks in all epics in the project
        epics_dir = safe_join_path('projects', project_id, 'epics')
        if os.path.exists(epics_dir):
            try:
                for epic_folder in os.listdir(epics_dir):
                    epic_path = os.path.join(epics_dir, epic_folder)
                    if os.path.isdir(epic_path):
                        tasks_dir = os.path.join(epic_path, 'tasks')
                        if os.path.exists(tasks_dir):
                            for task_folder in os.listdir(tasks_dir):
                                task_path = os.path.join(tasks_dir, task_folder)
                                if os.path.isdir(task_path):
                                    subtasks_dir = os.path.join(task_path, 'subtasks')
                                    if os.path.exists(subtasks_dir):
                                        for filename in os.listdir(subtasks_dir):
                                            if filename.endswith('.md'):
                                                subtask_id = filename[:-3]
                                                subtask_metadata, _ = load_subtask(project_id, epic_folder, task_folder, subtask_id, metadata_only=True)
                                                if subtask_metadata:
                                                    seq = subtask_metadata.get('seq_id', '')
                                                    if seq and seq.startswith('st'):
                                                        try:
                                                            num = int(seq[2:])
                                                            max_seq = max(max_seq, num)
                                                        except ValueError:
                                                            pass
            except OSError:
                pass
    
    return f'{prefix}{max_seq + 1}'


def load_project(project_id, metadata_only=False):
    """Load a project from disk."""
    if not is_valid_project_id(project_id):
        logger.warning(f"Invalid project ID: {project_id}")
        return None, None

    project_path = safe_join_path('projects', f'{project_id}.md')
    # Use sync manager for index-aware loading
    return sync_manager.load_entity_with_index(
        project_path, project_id, 'project', 
        'Untitled Project', 'active', metadata_only
    )


def save_project(project_id, metadata, content):
    """Save a project to disk."""
    if not is_valid_project_id(project_id):
        raise Http404("Invalid project ID")

    project_path = safe_join_path('projects', f'{project_id}.md')
    # Use sync manager to save and sync to index
    sync_manager.save_entity_with_sync(project_path, project_id, 'project', metadata, content)


def load_epic(project_id, epic_id, metadata_only=False):
    """Load an epic from disk."""
    if not is_valid_project_id(project_id) or not validate_id(epic_id, 'epic'):
        logger.warning(f"Invalid IDs: project={project_id}, epic={epic_id}")
        return None, None

    epic_path = safe_join_path('projects', project_id, 'epics', f'{epic_id}.md')
    # Add project_id to metadata for relationship tracking
    metadata, content = sync_manager.load_entity_with_index(
        epic_path, epic_id, 'epic', 
        'Untitled Epic', 'active', metadata_only
    )
    if metadata is not None and 'project_id' not in metadata:
        metadata['project_id'] = project_id
    return metadata, content


def save_epic(project_id, epic_id, metadata, content):
    """Save an epic to disk."""
    if not is_valid_project_id(project_id) or not validate_id(epic_id, 'epic'):
        raise Http404("Invalid IDs")

    epic_path = safe_join_path('projects', project_id, 'epics', f'{epic_id}.md')
    # Ensure project_id is in metadata for relationship tracking
    if 'project_id' not in metadata:
        metadata['project_id'] = project_id
    sync_manager.save_entity_with_sync(epic_path, epic_id, 'epic', metadata, content)
    update_project_stats(project_id)


def load_task(project_id, epic_id, task_id, metadata_only=False):
    """Load a task from disk."""
    if not (is_valid_project_id(project_id) and
            validate_id(epic_id, 'epic') and
            validate_id(task_id, 'task')):
        logger.warning(f"Invalid IDs: project={project_id}, epic={epic_id}, task={task_id}")
        return None, None

    task_path = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', f'{task_id}.md')
    # Add relationship IDs to metadata
    metadata, content = sync_manager.load_entity_with_index(
        task_path, task_id, 'task', 
        'Untitled Task', 'todo', metadata_only
    )
    if metadata is not None:
        if 'project_id' not in metadata:
            metadata['project_id'] = project_id
        if 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
    return metadata, content


def save_task(project_id, epic_id, task_id, metadata, content):
    """Save a task to disk."""
    if not (is_valid_project_id(project_id) and
            validate_id(epic_id, 'epic') and
            validate_id(task_id, 'task')):
        raise Http404("Invalid IDs")

    task_path = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', f'{task_id}.md')
    # Ensure relationship IDs are in metadata
    if 'project_id' not in metadata:
        metadata['project_id'] = project_id
    if 'epic_id' not in metadata:
        metadata['epic_id'] = epic_id
    sync_manager.save_entity_with_sync(task_path, task_id, 'task', metadata, content)
    update_project_stats(project_id)


def load_subtask(project_id, epic_id, task_id, subtask_id, metadata_only=False):
    """Load a subtask from disk."""
    if not (is_valid_project_id(project_id) and
            validate_id(epic_id, 'epic') and
            validate_id(task_id, 'task') and
            validate_id(subtask_id, 'subtask')):
        logger.warning(f"Invalid IDs: project={project_id}, epic={epic_id}, task={task_id}, subtask={subtask_id}")
        return None, None

    subtask_path = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks', f'{subtask_id}.md')
    # Add relationship IDs to metadata
    metadata, content = sync_manager.load_entity_with_index(
        subtask_path, subtask_id, 'subtask', 
        'Untitled Subtask', 'todo', metadata_only
    )
    if metadata is not None:
        if 'project_id' not in metadata:
            metadata['project_id'] = project_id
        if 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
        if 'task_id' not in metadata:
            metadata['task_id'] = task_id
    return metadata, content


def save_subtask(project_id, epic_id, task_id, subtask_id, metadata, content):
    """Save a subtask to disk."""
    if not (is_valid_project_id(project_id) and
            validate_id(epic_id, 'epic') and
            validate_id(task_id, 'task') and
            validate_id(subtask_id, 'subtask')):
        raise Http404("Invalid IDs")

    subtask_path = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks', f'{subtask_id}.md')
    # Ensure relationship IDs are in metadata
    if 'project_id' not in metadata:
        metadata['project_id'] = project_id
    if 'epic_id' not in metadata:
        metadata['epic_id'] = epic_id
    if 'task_id' not in metadata:
        metadata['task_id'] = task_id
    sync_manager.save_entity_with_sync(subtask_path, subtask_id, 'subtask', metadata, content)
    update_project_stats(project_id)


def compute_project_stats(project_id):
    """Compute project overview stats for list view."""
    epics_count = 0
    tasks_count = 0
    done_tasks_count = 0
    subtasks_count = 0
    done_subtasks_count = 0

    epics_dir = safe_join_path('projects', project_id, 'epics')
    if os.path.exists(epics_dir):
        epic_filenames = [f for f in os.listdir(epics_dir) if f.endswith('.md')]
        epics_count = len(epic_filenames)
        for e_filename in epic_filenames:
            epic_id = e_filename[:-3]
            tasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks')
            if os.path.exists(tasks_dir):
                task_filenames = [f for f in os.listdir(tasks_dir) if f.endswith('.md')]
                tasks_count += len(task_filenames)
                for t_filename in task_filenames:
                    task_id = t_filename[:-3]
                    t_metadata, _ = load_task(project_id, epic_id, task_id, metadata_only=True)
                    if t_metadata:
                        if t_metadata.get('status') == 'done':
                            done_tasks_count += 1

                        subtasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                        if os.path.exists(subtasks_dir):
                            subtask_filenames = [f for f in os.listdir(subtasks_dir) if f.endswith('.md')]
                            subtasks_count += len(subtask_filenames)
                            for s_filename in subtask_filenames:
                                subtask_id = s_filename[:-3]
                                s_metadata, _ = load_subtask(project_id, epic_id, task_id, subtask_id, metadata_only=True)
                                if s_metadata and s_metadata.get('status') == 'done':
                                    done_subtasks_count += 1

    completion_percentage = int((done_tasks_count / tasks_count) * 100) if tasks_count > 0 else 0

    return {
        'epics_count': epics_count,
        'tasks_count': tasks_count,
        'done_tasks_count': done_tasks_count,
        'subtasks_count': subtasks_count,
        'done_subtasks_count': done_subtasks_count,
        'completion_percentage': completion_percentage
    }


def update_project_stats(project_id):
    """Update cached stats in project metadata."""
    metadata, content = load_project(project_id)
    if metadata is None:
        return
    stats = compute_project_stats(project_id)
    metadata['stats'] = stats
    metadata['stats_version'] = STATS_VERSION
    metadata['stats_updated'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    save_project(project_id, metadata, content)


def handle_checklist_post(request, metadata):
    """Helper to handle checklist operations from POST request."""
    if 'checklist_add_title' in request.POST:
        title = request.POST.get('checklist_add_title', '').strip()
        if title:
            if 'checklist' not in metadata:
                metadata['checklist'] = []
            metadata['checklist'].append({
                'id': uuid.uuid4().hex[:8],
                'title': title,
                'status': 'todo'
            })
            return True
    
    if 'checklist_toggle_id' in request.POST:
        item_id = request.POST.get('checklist_toggle_id')
        for item in metadata.get('checklist', []):
            if item.get('id') == item_id:
                item['status'] = 'done' if item.get('status') == 'todo' else 'todo'
                return True
                
    if 'checklist_delete_id' in request.POST:
        item_id = request.POST.get('checklist_delete_id')
        if 'checklist' in metadata:
            metadata['checklist'] = [item for item in metadata['checklist'] if item.get('id') != item_id]
            return True
            
    return False


INBOX_PROJECT_ID = 'project-inbox'


def is_valid_project_id(project_id):
    """Check if project_id is valid, including special inbox project."""
    return project_id == INBOX_PROJECT_ID or validate_id(project_id, 'project')


def ensure_inbox_project():
    """Ensure the Inbox project exists, create it if it does not exist."""
    inbox_metadata, _ = load_project(INBOX_PROJECT_ID, metadata_only=True)
    if inbox_metadata is None:
        # Create inbox project
        color = get_project_color(INBOX_PROJECT_ID)
        metadata = {
            'title': 'Inbox',
            'status': 'active',
            'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'is_inbox': True,
            'color': color
        }
        save_project(INBOX_PROJECT_ID, metadata, 'Inbox for quick capture. File items here for later organization.')
    
    # Ensure inbox has a default epic for tasks
    epics_dir = safe_join_path('projects', INBOX_PROJECT_ID, 'epics')
    if os.path.exists(epics_dir):
        epic_files = [f for f in os.listdir(epics_dir) if f.endswith('.md')]
        if not epic_files:
            # Create default inbox epic
            inbox_epic_id = f'epic-{uuid.uuid4().hex[:8]}'
            seq_id = get_next_seq_id(INBOX_PROJECT_ID, 'epic')
            epic_metadata = {
                'title': 'Inbox',
                'status': 'active',
                'seq_id': seq_id,
                'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'is_inbox_epic': True
            }
            save_epic(INBOX_PROJECT_ID, inbox_epic_id, epic_metadata, 'Default epic for inbox tasks.')
    
    return INBOX_PROJECT_ID


def get_inbox_epic():
    """Get the default inbox epic ID. Creates it if it does not exist."""
    ensure_inbox_project()
    epics_dir = safe_join_path('projects', INBOX_PROJECT_ID, 'epics')
    if os.path.exists(epics_dir):
        for epic_file in os.listdir(epics_dir):
            if epic_file.endswith('.md'):
                epic_id = epic_file[:-3]
                epic_meta, _ = load_epic(INBOX_PROJECT_ID, epic_id, metadata_only=True)
                if epic_meta and epic_meta.get('is_inbox_epic'):
                    return epic_id
                # If no inbox epic found, use the first one
                if not os.path.exists(safe_join_path('projects', INBOX_PROJECT_ID, 'epics', f'{epic_id}.md')):
                    continue
                return epic_id
    # If no epic exists, create one
    inbox_epic_id = f'epic-{uuid.uuid4().hex[:8]}'
    seq_id = get_next_seq_id(INBOX_PROJECT_ID, 'epic')
    epic_metadata = {
        'title': 'Inbox',
        'status': 'active',
        'seq_id': seq_id,
        'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'is_inbox_epic': True
    }
    save_epic(INBOX_PROJECT_ID, inbox_epic_id, epic_metadata, 'Default epic for inbox tasks.')
    return inbox_epic_id


def project_list(request):
    """Display list of all projects."""
    projects_dir = safe_join_path('projects')
    os.makedirs(projects_dir, exist_ok=True)
    
    show_archived = request.GET.get('archived', 'false') == 'true'

    projects = []
    try:
        filenames = [f for f in os.listdir(projects_dir) if f.endswith('.md')]
        for filename in filenames:
            project_id = filename[:-3]
            # Skip inbox project - it is shown separately
            if project_id == INBOX_PROJECT_ID:
                continue
            metadata, _ = load_project(project_id, metadata_only=True)
            if metadata is not None:
                is_archived = metadata.get('archived', False)
                if (show_archived and not is_archived) or (not show_archived and is_archived):
                    continue
                
                stats = metadata.get('stats', {})
                if metadata.get('stats_version') != STATS_VERSION or not stats:
                    stats = compute_project_stats(project_id)
                    full_metadata, content = load_project(project_id, metadata_only=False)
                    if full_metadata is not None:
                        full_metadata['stats'] = stats
                        full_metadata['stats_version'] = STATS_VERSION
                        full_metadata['stats_updated'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
                        save_project(project_id, full_metadata, content)

                projects.append({
                    'id': project_id,
                    'title': metadata.get('title', 'Untitled Project'),
                    'status': metadata.get('status', 'active'),
                    'archived': is_archived,
                    'epics_count': stats.get('epics_count', 0),
                    'tasks_count': stats.get('tasks_count', 0),
                    'done_tasks_count': stats.get('done_tasks_count', 0),
                    'subtasks_count': stats.get('subtasks_count', 0),
                    'done_subtasks_count': stats.get('done_subtasks_count', 0),
                    'completion_percentage': stats.get('completion_percentage', 0)
                })
    except OSError as e:
        logger.error(f"Error reading projects directory: {e}")

    return render(request, 'pm/project_list.html', {
        'projects': projects,
        'show_archived': show_archived
    })


def new_project(request):
    """Create a new project."""
    if request.method == 'POST':
        title = request.POST.get('title', 'New Project')
        status = request.POST.get('status', 'active')
        priority = request.POST.get('priority', '').strip()
        content = request.POST.get('content', '')

        project_id = f'project-{uuid.uuid4().hex[:8]}'
        color = request.POST.get('color', '').strip()
        if not color:
            color = get_project_color(project_id)
        metadata = {
            'title': title,
            'status': status,
            'created': datetime.now().strftime('%Y-%m-%d'),
            'color': color
        }
        if priority:
            metadata['priority'] = priority

        save_project(project_id, metadata, content)

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'id': project_id,
                'title': title,
                'status': status,
                'priority': priority,
                'url': reverse('project_detail', kwargs={'project': project_id})
            })

        return redirect('project_detail', project=project_id)

    return render(request, 'pm/new_project.html')


def project_detail(request, project):
    """Display project details with epics and tasks."""
    metadata, content = load_project(project)
    if metadata is None:
        raise Http404("Project not found")

    # Handle checklist operations
    if request.method == 'POST' and handle_checklist_post(request, metadata):
        save_project(project, metadata, content)
        return redirect('project_detail', project=project)

    # Calculate project-level progress
    _, markdown_total, markdown_progress = calculate_markdown_progress(content)
    _, checklist_total, checklist_progress = calculate_checklist_progress(metadata)

    # Load epics and their tasks in a single pass
    epics_dir = safe_join_path('projects', project, 'epics')
    epics = []
    archived_epics = []
    open_epics = []

    if os.path.exists(epics_dir):
        try:
            # Get list of epic files once - filter out directories
            all_items = os.listdir(epics_dir)
            epic_filenames = [f for f in all_items if f.endswith('.md') and os.path.isfile(os.path.join(epics_dir, f))]
            
            for filename in epic_filenames:
                epic_id = filename[:-3]
                try:
                    # Epics in project_detail only need metadata
                    epic_metadata, _ = load_epic(project, epic_id, metadata_only=True)
                    if epic_metadata is None:
                        logger.warning(f"Could not load epic {epic_id} in project {project}")
                        continue
                    
                    is_archived = epic_metadata.get('archived', False)

                    # Load tasks for this epic
                    tasks_dir = safe_join_path('projects', project, 'epics', epic_id, 'tasks')
                    tasks = []
                    open_tasks = []
                    
                    if os.path.exists(tasks_dir):
                        try:
                            task_filenames = [f for f in os.listdir(tasks_dir) if f.endswith('.md') and os.path.isfile(os.path.join(tasks_dir, f))]
                            for task_filename in task_filenames:
                                task_id = task_filename[:-3]
                                try:
                                    # Tasks in project_detail only need metadata
                                    task_metadata, _ = load_task(project, epic_id, task_id, metadata_only=True)
                                    if task_metadata is None:
                                        continue
                                    
                                    task_data = {
                                        'id': task_id,
                                        'title': task_metadata.get('title', 'Untitled Task'),
                                        'status': task_metadata.get('status', 'todo'),
                                        'schedule_start': task_metadata.get('schedule_start', ''),
                                        'schedule_end': task_metadata.get('schedule_end', '')
                                    }
                                    tasks.append(task_data)
                                    
                                    # Check if it is an open task
                                    if task_data['status'] in ['todo', 'in_progress']:
                                        # Only load subtasks for open tasks if needed for the "Open Work" view
                                        subtasks_dir = safe_join_path('projects', project, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                                        open_subtasks = []
                                        if os.path.exists(subtasks_dir):
                                            try:
                                                subtask_filenames = [f for f in os.listdir(subtasks_dir) if f.endswith('.md') and os.path.isfile(os.path.join(subtasks_dir, f))]
                                                for subtask_filename in subtask_filenames:
                                                    subtask_id = subtask_filename[:-3]
                                                    try:
                                                        # Subtasks in project_detail only need metadata
                                                        subtask_metadata, _ = load_subtask(project, epic_id, task_id, subtask_id, metadata_only=True)
                                                        if subtask_metadata and subtask_metadata.get('status') in ['todo', 'in_progress']:
                                                            open_subtasks.append({
                                                                'id': subtask_id,
                                                                'title': subtask_metadata.get('title', 'Untitled Subtask'),
                                                                'status': subtask_metadata.get('status', 'todo')
                                                            })
                                                    except Exception as e:
                                                        logger.warning(f"Error loading subtask {subtask_id}: {e}")
                                                        continue
                                            except OSError as e:
                                                logger.warning(f"Error reading subtasks directory for task {task_id}: {e}")
                                        
                                        open_task_data = task_data.copy()
                                        open_task_data['subtasks'] = open_subtasks
                                        open_tasks.append(open_task_data)
                                except Exception as e:
                                    logger.warning(f"Error loading task {task_id} in epic {epic_id}: {e}")
                                    continue
                        except OSError as e:
                            logger.warning(f"Error reading tasks directory for epic {epic_id}: {e}")

                    # Calculate progress
                    total_tasks_count = len(tasks)
                    completed_tasks_count = sum(1 for t in tasks if t['status'] == 'done')
                    progress_pct = (completed_tasks_count / total_tasks_count * 100) if total_tasks_count > 0 else 0

                    epic_data = {
                        'id': epic_id,
                        'title': epic_metadata.get('title', 'Untitled Epic'),
                        'status': epic_metadata.get('status', 'active'),
                        'seq_id': epic_metadata.get('seq_id', ''),
                        'tasks': tasks,
                        'completed_tasks': completed_tasks_count,
                        'total_tasks': total_tasks_count,
                        'progress_percentage': progress_pct,
                        'archived': is_archived
                    }
                    
                    if is_archived:
                        archived_epics.append(epic_data)
                    else:
                        epics.append(epic_data)
                        # If epic is active, add to open_epics
                        if epic_data['status'] == 'active':
                            open_epic_data = epic_data.copy()
                            open_epic_data['tasks'] = open_tasks
                            open_epics.append(open_epic_data)
                except Exception as e:
                    logger.error(f"Error processing epic {epic_id} in project {project}: {e}")
                    continue
                    
            # Sort epics by seq_id, then by title
            epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
            archived_epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
            open_epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
                    
        except OSError as e:
            logger.error(f"Error reading epics directory: {e}")

    edit_mode = request.GET.get('edit', 'false') == 'true'

    # Handle archive/unarchive
    if request.method == 'POST' and 'archive' in request.POST:
        metadata['archived'] = True
        save_project(project, metadata, content)
        return redirect('project_list')
    
    if request.method == 'POST' and 'unarchive' in request.POST:
        metadata['archived'] = False
        save_project(project, metadata, content)
        return redirect('project_detail', project=project)

    if request.method == 'POST':
        # Handle form submission for editing
        metadata['title'] = request.POST.get('title', metadata['title'])
        metadata['status'] = request.POST.get('status', metadata['status'])
        color = request.POST.get('color', '').strip()
        if color:
            metadata['color'] = color
        elif 'color' not in metadata:
            metadata['color'] = get_project_color(project)
        content = request.POST.get('content', content)

        save_project(project, metadata, content)

        return redirect('project_detail', project=project)

    activity = get_project_activity(project)

    return render(request, 'pm/project_detail.html', {
        'metadata': metadata,
        'content': content,
        'project': project,
        'epics': epics,
        'archived_epics': archived_epics,
        'open_epics': open_epics,
        'activity': activity,
        'edit_mode': edit_mode,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total
    })


def new_epic(request, project):
    """Create a new epic."""
    if request.method == 'POST':
        title = request.POST.get('title', 'New Epic')
        status = request.POST.get('status', 'active')
        content = request.POST.get('content', '')

        epic_id = f'epic-{uuid.uuid4().hex[:8]}'
        seq_id = get_next_seq_id(project, 'epic')
        priority = request.POST.get('priority', '').strip() or '3'
        metadata = {
            'title': title,
            'status': status,
            'seq_id': seq_id,
            'priority': priority,
            'created': datetime.now().strftime('%Y-%m-%d')
        }

        # Add creation activity
        add_activity_entry(metadata, 'created')
        save_epic(project, epic_id, metadata, content)

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'id': epic_id,
                'seq_id': seq_id,
                'title': title,
                'status': status,
                'priority': priority,
                'url': reverse('epic_detail', kwargs={'project': project, 'epic': epic_id})
            })

        return redirect('epic_detail', project=project, epic=epic_id)

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project

    return render(request, 'pm/new_epic.html', {
        'project': project,
        'project_title': project_title
    })


def epic_detail(request, project, epic):
    """Display epic details with tasks."""
    metadata, content = load_epic(project, epic)
    if metadata is None:
        raise Http404("Epic not found")

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project

    # Handle archive/unarchive
    if request.method == 'POST' and 'archive' in request.POST:
        metadata['archived'] = True
        save_epic(project, epic, metadata, content)
        return redirect('project_detail', project=project)
    
    if request.method == 'POST' and 'unarchive' in request.POST:
        metadata['archived'] = False
        save_epic(project, epic, metadata, content)
        return redirect('epic_detail', project=project, epic=epic)

    # Handle checklist operations
    if request.method == 'POST' and handle_checklist_post(request, metadata):
        save_epic(project, epic, metadata, content)
        return redirect('epic_detail', project=project, epic=epic)

    # Calculate progress
    _, markdown_total, markdown_progress = calculate_markdown_progress(content)
    _, checklist_total, checklist_progress = calculate_checklist_progress(metadata)

    # Load tasks
    tasks_dir = safe_join_path('projects', project, 'epics', epic, 'tasks')
    tasks = []
    if os.path.exists(tasks_dir):
        try:
            for filename in os.listdir(tasks_dir):
                if filename.endswith('.md'):
                    task_id = filename[:-3]
                    task_metadata, _ = load_task(project, epic, task_id)
                    if task_metadata is not None:
                        tasks.append({
                            'id': task_id,
                            'title': task_metadata.get('title', 'Untitled Task'),
                            'status': task_metadata.get('status', 'todo'),
                            'seq_id': task_metadata.get('seq_id', ''),
                            'priority': task_metadata.get('priority', ''),
                            'created': task_metadata.get('created', ''),
                            'due_date': task_metadata.get('due_date', ''),
                            'order': task_metadata.get('order', 0)
                        })
        except OSError as e:
            logger.error(f"Error reading tasks directory: {e}")

    tasks.sort(key=lambda t: (t.get('order', 0), t.get('title', '')))

    # Calculate progress
    total_tasks = len(tasks)
    completed_tasks = sum(1 for task in tasks if task['status'] == 'done')
    progress_percentage = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

    edit_mode = request.GET.get('edit', 'false') == 'true'

    # Handle quick updates (status, priority, labels, people, dates)
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        if quick_update == 'status':
            old_status = metadata.get('status', 'active')
            new_status = request.POST.get('status', old_status)
            if old_status != new_status:
                metadata['status'] = new_status
                add_activity_entry(metadata, 'status_changed', old_status, new_status)
                save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'priority':
            old_priority = metadata.get('priority', '')
            priority = request.POST.get('priority', '').strip()
            if old_priority != priority:
                if priority:
                    metadata['priority'] = priority
                else:
                    metadata.pop('priority', None)
                add_activity_entry(metadata, 'priority_changed', old_priority, priority)
                save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'due_date':
            old_due = metadata.get('due_date', '')
            new_due = request.POST.get('due_date', '')
            if old_due != new_due:
                metadata['due_date'] = new_due
                add_activity_entry(metadata, 'due_date_changed', old_due, new_due)
                save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'add_label':
            label = request.POST.get('label', '').strip()
            if label:
                labels_list = normalize_labels(metadata.get('labels', []))
                if label not in labels_list:
                    labels_list.append(label)
                    metadata['labels'] = labels_list
                    add_activity_entry(metadata, 'label_added', None, label)
                    save_epic(project, epic, metadata, content)
                    cache.delete("all_labels:v1")  # Invalidate cache
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'remove_label':
            label = request.POST.get('label', '').strip()
            if label:
                labels_list = normalize_labels(metadata.get('labels', []))
                if label in labels_list:
                    labels_list = [l for l in labels_list if l != label]
                    metadata['labels'] = labels_list
                    add_activity_entry(metadata, 'label_removed', label, None)
                    save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'add_person':
            person = request.POST.get('person', '').strip()
            if person:
                people_list = normalize_people(metadata.get('people', []))
                if person not in people_list:
                    people_list.append(person)
                    metadata['people'] = people_list
                    add_activity_entry(metadata, 'person_added', None, person)
                    save_epic(project, epic, metadata, content)
                    cache.delete("all_people:v1")  # Invalidate cache
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'remove_person':
            person = request.POST.get('person', '').strip()
            if person:
                people_list = normalize_people(metadata.get('people', []))
                if person in people_list:
                    people_list = [p for p in people_list if p != person]
                    metadata['people'] = people_list
                    add_activity_entry(metadata, 'person_removed', person, None)
                    save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'add_note':
            note_id = request.POST.get('note_id', '').strip()
            if note_id:
                notes_list = metadata.get('notes', [])
                if note_id not in notes_list:
                    notes_list.append(note_id)
                    metadata['notes'] = notes_list
                    # Get note title for activity
                    note_meta, _ = load_note(note_id)
                    note_title = note_meta.get('title', note_id) if note_meta else note_id
                    add_activity_entry(metadata, 'note_linked', None, note_title)
                    save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)
        elif quick_update == 'remove_note':
            note_id = request.POST.get('note_id', '').strip()
            if note_id:
                notes_list = metadata.get('notes', [])
                if note_id in notes_list:
                    # Get note title for activity
                    note_meta, _ = load_note(note_id)
                    note_title = note_meta.get('title', note_id) if note_meta else note_id
                    metadata['notes'] = [n for n in notes_list if n != note_id]
                    add_activity_entry(metadata, 'note_unlinked', note_title, None)
                    save_epic(project, epic, metadata, content)
            return redirect('epic_detail', project=project, epic=epic)

    if request.method == 'POST':
        # Handle form submission for editing
        metadata['title'] = request.POST.get('title', metadata['title'])
        metadata['status'] = request.POST.get('status', metadata['status'])
        metadata['due_date'] = request.POST.get('due_date', metadata.get('due_date', ''))
        priority = request.POST.get('priority', '').strip()
        if priority:
            metadata['priority'] = priority
        else:
            metadata.pop('priority', None)
        content = request.POST.get('content', content)

        save_epic(project, epic, metadata, content)

        return redirect('epic_detail', project=project, epic=epic)

    # Prepare labels with colors
    labels_list = normalize_labels(metadata.get('labels', []))
    labels = [{'name': label, 'color': label_color(label)} for label in labels_list]
    labels_names = labels_list
    
    # Prepare people with colors
    people_list = normalize_people(metadata.get('people', []))
    people = []
    for p_name in people_list:
        # Check if this is actually a person ID (person- (7) + 8 hex = 15 chars)
        if p_name.startswith('person-') and len(p_name) == 15:
            # This is a person ID, not a name - load the person to get the actual name
            person_id = p_name
            person_meta, _ = load_person(person_id, metadata_only=True)
            if person_meta:
                actual_name = person_meta.get('name', '').strip()
                if actual_name and actual_name != person_id:
                    p_name = actual_name
                else:
                    # Person file exists but has no valid name, skip it
                    continue
            else:
                # Person ID does not exist, skip it
                continue
        else:
            # This is a name, find the person ID
            person_id = find_person_by_name(p_name)
            if person_id:
                # Load person to get their actual name (in case it changed)
                person_meta, _ = load_person(person_id, metadata_only=True)
                if person_meta:
                    actual_name = person_meta.get('name', '').strip()
                    if actual_name and actual_name != person_id:
                        p_name = actual_name
        person_id = find_person_by_name(p_name)
        if person_id:
            # Load person to get their actual name (in case it changed)
            person_meta, _ = load_person(person_id, metadata_only=True)
            if person_meta:
                actual_name = person_meta.get('name', '').strip()
                if actual_name and actual_name != person_id:
                    p_name = actual_name
        people.append({
            'name': p_name,
            'id': person_id if person_id else None,
            'color': label_color(p_name)
        })
    people_names = people_list
    
    # Get all labels and people for dropdowns (lazy-loaded, cached)
    all_labels = get_all_labels_in_system()
    all_people = get_all_people_names_in_system()  # Use names version for dropdowns

    # Load associated notes
    associated_notes = []
    note_ids = metadata.get('notes', [])
    for note_id in note_ids:
        note_meta, note_content = load_note(note_id)
        if note_meta:
            associated_notes.append({
                'id': note_id,
                'title': note_meta.get('title', 'Untitled Note'),
                'preview': (note_content or '')[:150]
            })
    
    # Get all notes for dropdown (exclude already associated)
    all_notes = get_all_notes_in_system()
    available_notes = [n for n in all_notes if n['id'] not in note_ids]

    # Check if this is the inbox epic
    is_inbox_epic = (project == INBOX_PROJECT_ID and metadata.get('is_inbox_epic', False))
    
    return render(request, 'pm/epic_detail.html', {
        'metadata': metadata,
        'content': content,
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'tasks': tasks,
        'completed_tasks': completed_tasks,
        'total_tasks': total_tasks,
        'progress_percentage': progress_percentage,
        'edit_mode': edit_mode,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total,
        'labels': labels,
        'labels_names': labels_names,
        'people': people,
        'people_names': people_names,
        'all_labels': all_labels,
        'all_people': all_people,  # This is now get_all_people_names_in_system() result
        'associated_notes': associated_notes,
        'available_notes': available_notes,
        'is_inbox_epic': is_inbox_epic
    })


def new_task(request, project, epic):
    """Create a new task."""
    if request.method == 'POST':
        title = request.POST.get('title', 'New Task')
        status = request.POST.get('status', 'todo')
        schedule_start = request.POST.get('schedule_start', '')
        schedule_end = request.POST.get('schedule_end', '')
        due_date = request.POST.get('due_date', '')
        labels = normalize_labels(request.POST.get('labels', ''))
        content = request.POST.get('content', '')

        task_id = f'task-{uuid.uuid4().hex[:8]}'
        seq_id = get_next_seq_id(project, 'task')
        priority = request.POST.get('priority', '').strip() or '3'
        metadata = {
            'title': title,
            'status': status,
            'seq_id': seq_id,
            'priority': priority,
            'schedule_start': schedule_start,
            'schedule_end': schedule_end,
            'due_date': due_date,
            'created': datetime.now().strftime('%Y-%m-%d')
        }
        if labels:
            metadata['labels'] = labels

        # Add creation activity
        add_activity_entry(metadata, 'created')
        save_task(project, epic, task_id, metadata, content)

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'id': task_id,
                'seq_id': seq_id,
                'title': title,
                'status': status,
                'priority': priority,
                'created': metadata.get('created', ''),
                'due_date': metadata.get('due_date', ''),
                'url': reverse('task_detail', kwargs={'project': project, 'epic': epic, 'task': task_id})
            })

        return redirect('task_detail', project=project, epic=epic, task=task_id)

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    epic_metadata, _ = load_epic(project, epic, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic

    return render(request, 'pm/new_task.html', {
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'epic_title': epic_title
    })


def task_detail(request, project, epic, task):
    """Display task details with subtasks and updates."""
    metadata, content = load_task(project, epic, task)
    if metadata is None:
        raise Http404("Task not found")

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    epic_metadata, _ = load_epic(project, epic, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic
    
    # Check if this task is in the inbox epic
    is_inbox_task = (project == INBOX_PROJECT_ID and epic_metadata and epic_metadata.get('is_inbox_epic', False))

    # Handle checklist operations
    if request.method == 'POST' and handle_checklist_post(request, metadata):
        save_task(project, epic, task, metadata, content)
        return redirect('task_detail', project=project, epic=epic, task=task)

    # Calculate progress
    _, markdown_total, markdown_progress = calculate_markdown_progress(content)
    _, checklist_total, checklist_progress = calculate_checklist_progress(metadata)

    # Handle subtask creation
    if request.method == 'POST' and 'subtask_title' in request.POST:
        subtask_title = request.POST.get('subtask_title', 'New Subtask')
        subtask_status = request.POST.get('subtask_status', 'todo')
        subtask_content = request.POST.get('subtask_content', '')

        subtask_id = f'subtask-{uuid.uuid4().hex[:8]}'
        subtask_metadata = {
            'title': subtask_title,
            'status': subtask_status,
            'created': datetime.now().strftime('%Y-%m-%d')
        }

        save_subtask(project, epic, task, subtask_id, subtask_metadata, subtask_content)

        # Handle AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'subtask': {
                    'id': subtask_id,
                    'title': subtask_title,
                    'status': subtask_status
                }
            })

        return redirect('task_detail', project=project, epic=epic, task=task)

    # Handle update submission
    if request.method == 'POST' and 'update_content' in request.POST:
        update_content = request.POST.get('update_content', '').strip()
        if update_content:
            # Add new update to metadata
            if 'updates' not in metadata:
                metadata['updates'] = []

            metadata['updates'].append({
                'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'content': update_content,
                'type': 'user'
            })

            save_task(project, epic, task, metadata, content)

        return redirect('task_detail', project=project, epic=epic, task=task)

    # Sort updates newest first
    updates = []
    for u in metadata.get('updates', []):
        update_copy = u.copy()
        if isinstance(update_copy['timestamp'], str):
            try:
                update_copy['timestamp'] = datetime.strptime(update_copy['timestamp'], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                pass
        # Default to 'user' type for backwards compatibility
        if 'type' not in update_copy:
            update_copy['type'] = 'user'
        updates.append(update_copy)
    
    updates.sort(key=lambda x: x['timestamp'] if isinstance(x['timestamp'], datetime) else str(x['timestamp']), reverse=True)

    # Load subtasks
    subtasks_dir = safe_join_path('projects', project, 'epics', epic, 'tasks', task, 'subtasks')
    subtasks = []
    if os.path.exists(subtasks_dir):
        try:
            for filename in os.listdir(subtasks_dir):
                if filename.endswith('.md'):
                    subtask_id = filename[:-3]
                    subtask_metadata, _ = load_subtask(project, epic, task, subtask_id)
                    if subtask_metadata is not None:
                        subtasks.append({
                            'id': subtask_id,
                            'seq_id': subtask_metadata.get('seq_id', ''),
                            'title': subtask_metadata.get('title', 'Untitled Subtask'),
                            'status': subtask_metadata.get('status', 'todo'),
                            'priority': subtask_metadata.get('priority', ''),
                            'created': subtask_metadata.get('created', ''),
                            'due_date': subtask_metadata.get('due_date', ''),
                            'order': subtask_metadata.get('order', 0)
                        })
        except OSError as e:
            logger.error(f"Error reading subtasks directory: {e}")

    subtasks.sort(key=lambda s: (s.get('order', 0), s.get('title', '')))

    edit_mode = request.GET.get('edit', 'false') == 'true'

    # Handle dependency operations
    if request.method == 'POST' and 'add_block' in request.POST:
        block_id = request.POST.get('add_block', '').strip()
        if block_id:
            if 'blocks' not in metadata:
                metadata['blocks'] = []
            if block_id not in metadata['blocks']:
                metadata['blocks'].append(block_id)
                # Get task title for activity
                available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=task)
                task_title = next((t['title'] for t in available_tasks if t['id'] == block_id), block_id)
                add_activity_entry(metadata, 'dependency_added', None, f"blocks {task_title}")
                # Update reciprocal: target should be blocked_by this task
                update_reciprocal_dependency(project, task, block_id, 'blocks', 'add')
            save_task(project, epic, task, metadata, content)
        return redirect('task_detail', project=project, epic=epic, task=task)
    
    if request.method == 'POST' and 'remove_block' in request.POST:
        block_id = request.POST.get('remove_block', '').strip()
        if block_id and 'blocks' in metadata and block_id in metadata['blocks']:
            # Get task title for activity
            available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=task)
            task_title = next((t['title'] for t in available_tasks if t['id'] == block_id), block_id)
            metadata['blocks'] = [b for b in metadata['blocks'] if b != block_id]
            add_activity_entry(metadata, 'dependency_removed', f"blocks {task_title}", None)
            # Update reciprocal: remove blocked_by from target
            update_reciprocal_dependency(project, task, block_id, 'blocks', 'remove')
            save_task(project, epic, task, metadata, content)
        return redirect('task_detail', project=project, epic=epic, task=task)
    
    if request.method == 'POST' and 'add_blocked_by' in request.POST:
        blocked_by_id = request.POST.get('add_blocked_by', '').strip()
        if blocked_by_id:
            if 'blocked_by' not in metadata:
                metadata['blocked_by'] = []
            if blocked_by_id not in metadata['blocked_by']:
                metadata['blocked_by'].append(blocked_by_id)
                # Get task title for activity
                available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=task)
                task_title = next((t['title'] for t in available_tasks if t['id'] == blocked_by_id), blocked_by_id)
                add_activity_entry(metadata, 'dependency_added', None, f"blocked by {task_title}")
                # Update reciprocal: target should block this task
                update_reciprocal_dependency(project, task, blocked_by_id, 'blocked_by', 'add')
            save_task(project, epic, task, metadata, content)
        return redirect('task_detail', project=project, epic=epic, task=task)
    
    if request.method == 'POST' and 'remove_blocked_by' in request.POST:
        blocked_by_id = request.POST.get('remove_blocked_by', '').strip()
        if blocked_by_id and 'blocked_by' in metadata and blocked_by_id in metadata['blocked_by']:
            # Get task title for activity
            available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=task)
            task_title = next((t['title'] for t in available_tasks if t['id'] == blocked_by_id), blocked_by_id)
            metadata['blocked_by'] = [b for b in metadata['blocked_by'] if b != blocked_by_id]
            add_activity_entry(metadata, 'dependency_removed', f"blocked by {task_title}", None)
            # Update reciprocal: remove blocks from target
            update_reciprocal_dependency(project, task, blocked_by_id, 'blocked_by', 'remove')
            save_task(project, epic, task, metadata, content)
        return redirect('task_detail', project=project, epic=epic, task=task)

    # Handle quick updates (status, priority, schedule)
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        if quick_update == 'status':
            old_status = metadata.get('status', 'todo')
            new_status = request.POST.get('status', old_status)
            if old_status != new_status:
                metadata['status'] = new_status
                add_activity_entry(metadata, 'status_changed', old_status, new_status)
                save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'priority':
            old_priority = metadata.get('priority', '')
            priority = request.POST.get('priority', '').strip()
            if old_priority != priority:
                if priority:
                    metadata['priority'] = priority
                else:
                    metadata.pop('priority', None)
                add_activity_entry(metadata, 'priority_changed', old_priority, priority)
                save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'schedule_start':
            old_start = metadata.get('schedule_start', '')
            new_start = request.POST.get('schedule_start', '')
            if old_start != new_start:
                metadata['schedule_start'] = new_start
                add_activity_entry(metadata, 'schedule_start_changed', old_start, new_start)
                save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'schedule_end':
            old_end = metadata.get('schedule_end', '')
            new_end = request.POST.get('schedule_end', '')
            if old_end != new_end:
                metadata['schedule_end'] = new_end
                add_activity_entry(metadata, 'schedule_end_changed', old_end, new_end)
                save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'due_date':
            old_due = metadata.get('due_date', '')
            new_due = request.POST.get('due_date', '')
            if old_due != new_due:
                metadata['due_date'] = new_due
                add_activity_entry(metadata, 'due_date_changed', old_due, new_due)
                save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'add_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label not in current_labels:
                    current_labels.append(label)
                    metadata['labels'] = current_labels
                    add_activity_entry(metadata, 'label_added', None, label)
                    save_task(project, epic, task, metadata, content)
                    cache.delete("all_labels:v1")  # Invalidate cache
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'remove_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label in current_labels:
                    metadata['labels'] = [l for l in current_labels if l != label]
                    add_activity_entry(metadata, 'label_removed', label, None)
                    save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'add_person':
            person = request.POST.get('person', '').strip()
            if person:
                current_people = normalize_people(metadata.get('people', []))
                if person not in current_people:
                    current_people.append(person)
                    metadata['people'] = current_people
                    add_activity_entry(metadata, 'person_added', None, person)
                    save_task(project, epic, task, metadata, content)
                    cache.delete("all_people:v1")  # Invalidate cache
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'remove_person':
            person = request.POST.get('person', '').strip()
            if person:
                current_people = normalize_people(metadata.get('people', []))
                if person in current_people:
                    metadata['people'] = [p for p in current_people if p != person]
                    add_activity_entry(metadata, 'person_removed', person, None)
                    save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'add_note':
            note_id = request.POST.get('note_id', '').strip()
            if note_id:
                notes_list = metadata.get('notes', [])
                if note_id not in notes_list:
                    notes_list.append(note_id)
                    metadata['notes'] = notes_list
                    # Get note title for activity
                    note_meta, _ = load_note(note_id)
                    note_title = note_meta.get('title', note_id) if note_meta else note_id
                    add_activity_entry(metadata, 'note_linked', None, note_title)
                    save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)
        elif quick_update == 'remove_note':
            note_id = request.POST.get('note_id', '').strip()
            if note_id:
                notes_list = metadata.get('notes', [])
                if note_id in notes_list:
                    # Get note title for activity
                    note_meta, _ = load_note(note_id)
                    note_title = note_meta.get('title', note_id) if note_meta else note_id
                    metadata['notes'] = [n for n in notes_list if n != note_id]
                    add_activity_entry(metadata, 'note_unlinked', note_title, None)
                    save_task(project, epic, task, metadata, content)
            return redirect('task_detail', project=project, epic=epic, task=task)

    if request.method == 'POST' and 'title' in request.POST:
        # Handle form submission for editing
        metadata['title'] = request.POST.get('title', metadata['title'])
        metadata['status'] = request.POST.get('status', metadata['status'])
        metadata['schedule_start'] = request.POST.get('schedule_start', metadata.get('schedule_start', ''))
        metadata['schedule_end'] = request.POST.get('schedule_end', metadata.get('schedule_end', ''))
        metadata['due_date'] = request.POST.get('due_date', metadata.get('due_date', ''))
        priority = request.POST.get('priority', '').strip()
        if priority:
            metadata['priority'] = priority
        else:
            metadata.pop('priority', None)
        labels = normalize_labels(request.POST.get('labels', ''))
        if labels:
            metadata['labels'] = labels
        else:
            metadata.pop('labels', None)
        people = normalize_people(request.POST.get('people', ''))
        if people:
            metadata['people'] = people
        else:
            metadata.pop('people', None)
        content = request.POST.get('content', content)

        save_task(project, epic, task, metadata, content)

        return redirect('task_detail', project=project, epic=epic, task=task)

    labels_list = normalize_labels(metadata.get('labels', []))
    labels_with_colors = [{'name': l, 'color': label_color(l)} for l in labels_list]

    # Calculate subtask progress
    subtask_total = len(subtasks)
    subtask_done = sum(1 for s in subtasks if s.get('status') == 'done')
    subtask_progress = int((subtask_done / subtask_total * 100)) if subtask_total > 0 else 0
    
    # Calculate overall task progress (checklist + subtasks)
    total_items = checklist_total + subtask_total
    checklist_done = sum(1 for item in metadata.get('checklist', []) if item.get('status') == 'done')
    done_items = checklist_done + subtask_done
    overall_progress = int((done_items / total_items * 100)) if total_items > 0 else 0
    
    # Get people assigned to this task
    people_list = normalize_people(metadata.get('people', []))
    people_with_colors = []
    for p_name in people_list:
        # Check if this is actually a person ID (person- (7) + 8 hex = 15 chars)
        if p_name.startswith('person-') and len(p_name) == 15:
            # This is a person ID, not a name - load the person to get the actual name
            person_id = p_name
            person_meta, _ = load_person(person_id, metadata_only=True)
            if person_meta:
                actual_name = person_meta.get('name', '').strip()
                if actual_name and actual_name != person_id:
                    p_name = actual_name
                else:
                    # Person file exists but has no valid name, skip it
                    continue
            else:
                # Person ID does not exist, skip it
                continue
        else:
            # This is a name, find the person ID
            person_id = find_person_by_name(p_name)
            if person_id:
                # Load person to get their actual name (in case it changed)
                person_meta, _ = load_person(person_id, metadata_only=True)
                if person_meta:
                    actual_name = person_meta.get('name', '').strip()
                    if actual_name and actual_name != person_id:
                        p_name = actual_name
        
        people_with_colors.append({
            'name': p_name,
            'id': person_id if person_id else None,
            'color': label_color(p_name)
        })

    # Load dependencies
    blocks = []
    blocked_by = []
    available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=task)
    
    # Resolve blocks (tasks this task blocks)
    for block_id in metadata.get('blocks', []):
        for t in available_tasks:
            if t['id'] == block_id:
                blocks.append(t)
                break
    
    # Resolve blocked_by (tasks that block this task)
    for blocked_by_id in metadata.get('blocked_by', []):
        for t in available_tasks:
            if t['id'] == blocked_by_id:
                blocked_by.append(t)
                break

    # Load associated notes
    associated_notes = []
    note_ids = metadata.get('notes', [])
    for note_id in note_ids:
        note_meta, note_content = load_note(note_id)
        if note_meta:
            associated_notes.append({
                'id': note_id,
                'title': note_meta.get('title', 'Untitled Note'),
                'preview': (note_content or '')[:150]
            })
    
    # Get all notes for dropdown (exclude already associated)
    all_notes = get_all_notes_in_system()
    available_notes = [n for n in all_notes if n['id'] not in note_ids]

    return render(request, 'pm/task_detail.html', {
        'metadata': metadata,
        'content': content,
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'epic_title': epic_title,
        'epic_metadata': epic_metadata,
        'task': task,
        'subtasks': subtasks,
        'updates': updates,
        'labels': labels_with_colors,
        'labels_names': labels_list,
        'people': people_with_colors,
        'people_names': people_list,
        'all_labels': get_all_labels_in_system(),
        'all_people': get_all_people_names_in_system(),
        'blocks': blocks,
        'blocked_by': blocked_by,
        'available_tasks': available_tasks,
        'associated_notes': associated_notes,
        'available_notes': available_notes,
        'edit_mode': edit_mode,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total,
        'subtask_total': subtask_total,
        'subtask_done': subtask_done,
        'subtask_progress': subtask_progress,
        'overall_progress': overall_progress,
        'total_items': total_items,
        'is_inbox_task': is_inbox_task
    })


def new_subtask(request, project, epic, task):
    """Create a new subtask."""
    if request.method == 'POST':
        title = request.POST.get('title', 'New Subtask')
        status = request.POST.get('status', 'todo')
        due_date = request.POST.get('due_date', '')
        labels = normalize_labels(request.POST.get('labels', ''))
        content = request.POST.get('content', '')

        subtask_id = f'subtask-{uuid.uuid4().hex[:8]}'
        seq_id = get_next_seq_id(project, 'subtask')
        priority = request.POST.get('priority', '').strip() or '3'
        metadata = {
            'title': title,
            'status': status,
            'seq_id': seq_id,
            'priority': priority,
            'due_date': due_date,
            'created': datetime.now().strftime('%Y-%m-%d')
        }
        if labels:
            metadata['labels'] = labels

        # Add creation activity
        add_activity_entry(metadata, 'created')
        save_subtask(project, epic, task, subtask_id, metadata, content)

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'id': subtask_id,
                'seq_id': seq_id,
                'title': title,
                'status': status,
                'priority': priority,
                'created': metadata.get('created', ''),
                'due_date': metadata.get('due_date', ''),
                'url': reverse('subtask_detail', kwargs={'project': project, 'epic': epic, 'task': task, 'subtask': subtask_id})
            })

        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask_id)

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    epic_metadata, _ = load_epic(project, epic, metadata_only=True)
    task_metadata, _ = load_task(project, epic, task, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic
    task_title = task_metadata.get('title', 'Untitled Task') if task_metadata else task

    return render(request, 'pm/new_subtask.html', {
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'epic_title': epic_title,
        'task': task,
        'task_title': task_title
    })


def subtask_detail(request, project, epic, task, subtask):
    """Display subtask details with updates."""
    metadata, content = load_subtask(project, epic, task, subtask)
    if metadata is None:
        raise Http404("Subtask not found")

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    epic_metadata, _ = load_epic(project, epic, metadata_only=True)
    task_metadata, _ = load_task(project, epic, task, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic
    task_title = task_metadata.get('title', 'Untitled Task') if task_metadata else task

    # Handle checklist operations
    if request.method == 'POST' and handle_checklist_post(request, metadata):
        save_subtask(project, epic, task, subtask, metadata, content)
        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)

    # Calculate progress
    _, markdown_total, markdown_progress = calculate_markdown_progress(content)
    _, checklist_total, checklist_progress = calculate_checklist_progress(metadata)

    # Handle update submission
    if request.method == 'POST' and 'update_content' in request.POST:
        update_content = request.POST.get('update_content', '').strip()
        if update_content:
            # Add new update to metadata
            if 'updates' not in metadata:
                metadata['updates'] = []

            metadata['updates'].append({
                'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'content': update_content,
                'type': 'user'
            })

            save_subtask(project, epic, task, subtask, metadata, content)

        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)

    # Sort updates newest first
    updates = []
    for u in metadata.get('updates', []):
        update_copy = u.copy()
        if isinstance(update_copy['timestamp'], str):
            try:
                update_copy['timestamp'] = datetime.strptime(update_copy['timestamp'], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                pass
        # Default to 'user' type for backwards compatibility
        if 'type' not in update_copy:
            update_copy['type'] = 'user'
        updates.append(update_copy)
    
    updates.sort(key=lambda x: x['timestamp'] if isinstance(x['timestamp'], datetime) else str(x['timestamp']), reverse=True)

    edit_mode = request.GET.get('edit', 'false') == 'true'

    # Handle dependency operations
    if request.method == 'POST' and 'add_block' in request.POST:
        block_id = request.POST.get('add_block', '').strip()
        if block_id:
            if 'blocks' not in metadata:
                metadata['blocks'] = []
            if block_id not in metadata['blocks']:
                metadata['blocks'].append(block_id)
                # Get task title for activity
                available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=subtask)
                task_title = next((t['title'] for t in available_tasks if t['id'] == block_id), block_id)
                add_activity_entry(metadata, 'dependency_added', None, f"blocks {task_title}")
                # Update reciprocal: target should be blocked_by this subtask
                update_reciprocal_dependency(project, subtask, block_id, 'blocks', 'add')
            save_subtask(project, epic, task, subtask, metadata, content)
        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
    
    if request.method == 'POST' and 'remove_block' in request.POST:
        block_id = request.POST.get('remove_block', '').strip()
        if block_id and 'blocks' in metadata and block_id in metadata['blocks']:
            # Get task title for activity
            available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=subtask)
            task_title = next((t['title'] for t in available_tasks if t['id'] == block_id), block_id)
            metadata['blocks'] = [b for b in metadata['blocks'] if b != block_id]
            add_activity_entry(metadata, 'dependency_removed', f"blocks {task_title}", None)
            # Update reciprocal: remove blocked_by from target
            update_reciprocal_dependency(project, subtask, block_id, 'blocks', 'remove')
            save_subtask(project, epic, task, subtask, metadata, content)
        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
    
    if request.method == 'POST' and 'add_blocked_by' in request.POST:
        blocked_by_id = request.POST.get('add_blocked_by', '').strip()
        if blocked_by_id:
            if 'blocked_by' not in metadata:
                metadata['blocked_by'] = []
            if blocked_by_id not in metadata['blocked_by']:
                metadata['blocked_by'].append(blocked_by_id)
                # Get task title for activity
                available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=subtask)
                task_title = next((t['title'] for t in available_tasks if t['id'] == blocked_by_id), blocked_by_id)
                add_activity_entry(metadata, 'dependency_added', None, f"blocked by {task_title}")
                # Update reciprocal: target should block this subtask
                update_reciprocal_dependency(project, subtask, blocked_by_id, 'blocked_by', 'add')
            save_subtask(project, epic, task, subtask, metadata, content)
        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
    
    if request.method == 'POST' and 'remove_blocked_by' in request.POST:
        blocked_by_id = request.POST.get('remove_blocked_by', '').strip()
        if blocked_by_id and 'blocked_by' in metadata and blocked_by_id in metadata['blocked_by']:
            # Get task title for activity
            available_tasks = get_project_tasks_for_dependencies(project, exclude_task_id=subtask)
            task_title = next((t['title'] for t in available_tasks if t['id'] == blocked_by_id), blocked_by_id)
            metadata['blocked_by'] = [b for b in metadata['blocked_by'] if b != blocked_by_id]
            add_activity_entry(metadata, 'dependency_removed', f"blocked by {task_title}", None)
            # Update reciprocal: remove blocks from target
            update_reciprocal_dependency(project, subtask, blocked_by_id, 'blocked_by', 'remove')
            save_subtask(project, epic, task, subtask, metadata, content)
        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)

    # Handle quick updates (status, priority, schedule)
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        if quick_update == 'status':
            old_status = metadata.get('status', 'todo')
            new_status = request.POST.get('status', old_status)
            if old_status != new_status:
                metadata['status'] = new_status
                add_activity_entry(metadata, 'status_changed', old_status, new_status)
                save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'priority':
            old_priority = metadata.get('priority', '')
            priority = request.POST.get('priority', '').strip()
            if old_priority != priority:
                if priority:
                    metadata['priority'] = priority
                else:
                    metadata.pop('priority', None)
                add_activity_entry(metadata, 'priority_changed', old_priority, priority)
                save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'schedule_start':
            old_start = metadata.get('schedule_start', '')
            new_start = request.POST.get('schedule_start', '')
            if old_start != new_start:
                metadata['schedule_start'] = new_start
                add_activity_entry(metadata, 'schedule_start_changed', old_start, new_start)
                save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'schedule_end':
            old_end = metadata.get('schedule_end', '')
            new_end = request.POST.get('schedule_end', '')
            if old_end != new_end:
                metadata['schedule_end'] = new_end
                add_activity_entry(metadata, 'schedule_end_changed', old_end, new_end)
                save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'due_date':
            old_due = metadata.get('due_date', '')
            new_due = request.POST.get('due_date', '')
            if old_due != new_due:
                metadata['due_date'] = new_due
                add_activity_entry(metadata, 'due_date_changed', old_due, new_due)
                save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'add_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label not in current_labels:
                    current_labels.append(label)
                    metadata['labels'] = current_labels
                    add_activity_entry(metadata, 'label_added', None, label)
                    save_subtask(project, epic, task, subtask, metadata, content)
                    cache.delete("all_labels:v1")  # Invalidate cache
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'remove_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label in current_labels:
                    metadata['labels'] = [l for l in current_labels if l != label]
                    add_activity_entry(metadata, 'label_removed', label, None)
                    save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'add_person':
            person = request.POST.get('person', '').strip()
            if person:
                current_people = normalize_people(metadata.get('people', []))
                if person not in current_people:
                    current_people.append(person)
                    metadata['people'] = current_people
                    add_activity_entry(metadata, 'person_added', None, person)
                    save_subtask(project, epic, task, subtask, metadata, content)
                    cache.delete("all_people:v1")  # Invalidate cache
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'remove_person':
            person = request.POST.get('person', '').strip()
            if person:
                current_people = normalize_people(metadata.get('people', []))
                if person in current_people:
                    metadata['people'] = [p for p in current_people if p != person]
                    add_activity_entry(metadata, 'person_removed', person, None)
                    save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'add_note':
            note_id = request.POST.get('note_id', '').strip()
            if note_id:
                notes_list = metadata.get('notes', [])
                if note_id not in notes_list:
                    notes_list.append(note_id)
                    metadata['notes'] = notes_list
                    # Get note title for activity
                    note_meta, _ = load_note(note_id)
                    note_title = note_meta.get('title', note_id) if note_meta else note_id
                    add_activity_entry(metadata, 'note_linked', None, note_title)
                    save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        elif quick_update == 'remove_note':
            note_id = request.POST.get('note_id', '').strip()
            if note_id:
                notes_list = metadata.get('notes', [])
                if note_id in notes_list:
                    # Get note title for activity
                    note_meta, _ = load_note(note_id)
                    note_title = note_meta.get('title', note_id) if note_meta else note_id
                    metadata['notes'] = [n for n in notes_list if n != note_id]
                    add_activity_entry(metadata, 'note_unlinked', note_title, None)
                    save_subtask(project, epic, task, subtask, metadata, content)
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)

    if request.method == 'POST' and 'title' in request.POST:
        # Handle form submission for editing
        metadata['title'] = request.POST.get('title', metadata['title'])
        metadata['status'] = request.POST.get('status', metadata['status'])
        metadata['due_date'] = request.POST.get('due_date', metadata.get('due_date', ''))
        priority = request.POST.get('priority', '').strip()
        if priority:
            metadata['priority'] = priority
        else:
            metadata.pop('priority', None)
        labels = normalize_labels(request.POST.get('labels', ''))
        if labels:
            metadata['labels'] = labels
        else:
            metadata.pop('labels', None)
        content = request.POST.get('content', content)

        save_subtask(project, epic, task, subtask, metadata, content)

        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)

    labels_list = normalize_labels(metadata.get('labels', []))
    labels_with_colors = [{'name': l, 'color': label_color(l)} for l in labels_list]
    
    people_list = normalize_people(metadata.get('people', []))
    people_with_colors = []
    for p_name in people_list:
        # Check if this is actually a person ID (person- (7) + 8 hex = 15 chars)
        if p_name.startswith('person-') and len(p_name) == 15:
            # This is a person ID, not a name - load the person to get the actual name
            person_id = p_name
            person_meta, _ = load_person(person_id, metadata_only=True)
            if person_meta:
                actual_name = person_meta.get('name', '').strip()
                if actual_name and actual_name != person_id:
                    p_name = actual_name
                else:
                    # Person file exists but has no valid name, skip it
                    continue
            else:
                # Person ID does not exist, skip it
                continue
        else:
            # This is a name, find the person ID
            person_id = find_person_by_name(p_name)
            if person_id:
                # Load person to get their actual name (in case it changed)
                person_meta, _ = load_person(person_id, metadata_only=True)
                if person_meta:
                    actual_name = person_meta.get('name', '').strip()
                    if actual_name and actual_name != person_id:
                        p_name = actual_name
        
        people_with_colors.append({
            'name': p_name,
            'id': person_id if person_id else None,
            'color': label_color(p_name)
        })

    # Load dependencies
    blocks = []
    blocked_by = []
    available_tasks = get_project_tasks_for_dependencies(project, exclude_subtask_id=subtask)
    
    # Resolve blocks
    for block_id in metadata.get('blocks', []):
        for t in available_tasks:
            if t['id'] == block_id:
                blocks.append(t)
                break
    
    # Resolve blocked_by
    for blocked_by_id in metadata.get('blocked_by', []):
        for t in available_tasks:
            if t['id'] == blocked_by_id:
                blocked_by.append(t)
                break

    # Load associated notes
    associated_notes = []
    note_ids = metadata.get('notes', [])
    for note_id in note_ids:
        note_meta, note_content = load_note(note_id)
        if note_meta:
            associated_notes.append({
                'id': note_id,
                'title': note_meta.get('title', 'Untitled Note'),
                'preview': (note_content or '')[:150]
            })
    
    # Get all notes for dropdown (exclude already associated)
    all_notes = get_all_notes_in_system()
    available_notes = [n for n in all_notes if n['id'] not in note_ids]

    return render(request, 'pm/subtask_detail.html', {
        'metadata': metadata,
        'content': content,
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'epic_title': epic_title,
        'task': task,
        'task_title': task_title,
        'subtask': subtask,
        'updates': updates,
        'labels': labels_with_colors,
        'labels_names': labels_list,
        'people': people_with_colors,
        'people_names': people_list,
        'all_labels': get_all_labels_in_system(),
        'all_people': get_all_people_names_in_system(),
        'blocks': blocks,
        'blocked_by': blocked_by,
        'available_tasks': available_tasks,
        'associated_notes': associated_notes,
        'available_notes': available_notes,
        'edit_mode': edit_mode,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total
    })


def get_all_scheduled_tasks():
    """Helper to find all tasks with a schedule across all projects."""
    cache_key = "scheduled_tasks:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    projects_dir = safe_join_path('projects')
    scheduled_tasks = []
    
    if not os.path.exists(projects_dir):
        return []
        
    try:
        # Projects are directories in projects_dir
        for p_id in os.listdir(projects_dir):
            p_dir_path = os.path.join(projects_dir, p_id)
            if not os.path.isdir(p_dir_path):
                continue
            
            epics_dir = os.path.join(p_dir_path, 'epics')
            if os.path.exists(epics_dir):
                for e_id in os.listdir(epics_dir):
                    e_dir_path = os.path.join(epics_dir, e_id)
                    if not os.path.isdir(e_dir_path):
                        continue
                    
                    tasks_dir = os.path.join(e_dir_path, 'tasks')
                    if os.path.exists(tasks_dir):
                        for t_filename in os.listdir(tasks_dir):
                            if t_filename.endswith('.md'):
                                t_id = t_filename[:-3]
                                t_metadata, _ = load_task(p_id, e_id, t_id, metadata_only=True)
                                if t_metadata and (t_metadata.get('schedule_start') or t_metadata.get('schedule_end')):
                                    # Get project color
                                    p_metadata, _ = load_project(p_id, metadata_only=True)
                                    project_color = get_project_color(p_id, p_metadata.get('color') if p_metadata else None)
                                    scheduled_tasks.append({
                                        'id': t_id,
                                        'project_id': p_id,
                                        'epic_id': e_id,
                                        'title': t_metadata.get('title', 'Untitled Task'),
                                        'seq_id': t_metadata.get('seq_id', ''),
                                        'status': t_metadata.get('status', 'todo'),
                                        'schedule_start': t_metadata.get('schedule_start', ''),
                                        'schedule_end': t_metadata.get('schedule_end', ''),
                                        'project_color': project_color,
                                        'project_color_bg': hex_to_rgba(project_color, 0.15)
                                    })
    except OSError as e:
        logger.error(f"Error scanning for scheduled tasks: {e}")
        
    cache.set(cache_key, scheduled_tasks, 30)
    return scheduled_tasks


def get_all_projects_hierarchy():
    """Get all projects with their epics, tasks, and subtasks for the sidebar."""
    cache_key = "projects_hierarchy:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    projects_dir = safe_join_path('projects')
    projects = []
    
    if not os.path.exists(projects_dir):
        return []
        
    try:
        for p_id in os.listdir(projects_dir):
            p_dir_path = os.path.join(projects_dir, p_id)
            if not os.path.isdir(p_dir_path):
                continue
            
            p_metadata, _ = load_project(p_id, metadata_only=True)
            if p_metadata is None:
                continue
            
            project_data = {
                'id': p_id,
                'title': p_metadata.get('title', 'Untitled Project'),
                'epics': []
            }
            
            epics_dir = os.path.join(p_dir_path, 'epics')
            if os.path.exists(epics_dir):
                for e_id in os.listdir(epics_dir):
                    e_dir_path = os.path.join(epics_dir, e_id)
                    if not os.path.isdir(e_dir_path):
                        continue
                    
                    e_metadata, _ = load_epic(p_id, e_id, metadata_only=True)
                    if e_metadata is None:
                        continue
                    
                    epic_data = {
                        'id': e_id,
                        'title': e_metadata.get('title', 'Untitled Epic'),
                        'seq_id': e_metadata.get('seq_id', ''),
                        'tasks': []
                    }
                    
                    tasks_dir = os.path.join(e_dir_path, 'tasks')
                    if os.path.exists(tasks_dir):
                        for t_filename in os.listdir(tasks_dir):
                            if t_filename.endswith('.md'):
                                t_id = t_filename[:-3]
                                t_metadata, _ = load_task(p_id, e_id, t_id, metadata_only=True)
                                if t_metadata is None:
                                    continue
                                
                                task_data = {
                                    'id': t_id,
                                    'title': t_metadata.get('title', 'Untitled Task'),
                                    'seq_id': t_metadata.get('seq_id', ''),
                                    'status': t_metadata.get('status', 'todo'),
                                    'subtasks': []
                                }
                                
                                subtasks_dir = os.path.join(tasks_dir, t_id, 'subtasks')
                                if os.path.exists(subtasks_dir):
                                    for s_filename in os.listdir(subtasks_dir):
                                        if s_filename.endswith('.md'):
                                            s_id = s_filename[:-3]
                                            s_metadata, _ = load_subtask(p_id, e_id, t_id, s_id, metadata_only=True)
                                            if s_metadata:
                                                task_data['subtasks'].append({
                                                    'id': s_id,
                                                    'title': s_metadata.get('title', 'Untitled Subtask'),
                                                    'status': s_metadata.get('status', 'todo')
                                                })
                                
                                epic_data['tasks'].append(task_data)
                    
                    project_data['epics'].append(epic_data)
            
            projects.append(project_data)
    except OSError as e:
        logger.error(f"Error loading projects hierarchy: {e}")
    
    cache.set(cache_key, projects, 30)
    return projects


def parse_date_safe(value):
    """Parse date or datetime string safely."""
    if not value:
        return None
    try:
        if 'T' in value:
            return datetime.fromisoformat(value).date()
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def get_all_work_items():
    """Return all tasks and subtasks with metadata for work views."""
    cache_key = "work_items:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    projects_dir = safe_join_path('projects')
    items = []

    if not os.path.exists(projects_dir):
        return items

    try:
        for p_id in os.listdir(projects_dir):
            p_dir_path = os.path.join(projects_dir, p_id)
            if not os.path.isdir(p_dir_path):
                continue

            epics_dir = os.path.join(p_dir_path, 'epics')
            if not os.path.exists(epics_dir):
                continue

            for e_id in os.listdir(epics_dir):
                e_dir_path = os.path.join(epics_dir, e_id)
                if not os.path.isdir(e_dir_path):
                    continue

                tasks_dir = os.path.join(e_dir_path, 'tasks')
                if not os.path.exists(tasks_dir):
                    continue

                for t_filename in os.listdir(tasks_dir):
                    if not t_filename.endswith('.md'):
                        continue
                    t_id = t_filename[:-3]
                    t_metadata, _ = load_task(p_id, e_id, t_id, metadata_only=True)
                    if t_metadata:
                        items.append({
                            'type': 'task',
                            'id': t_id,
                            'title': t_metadata.get('title', 'Untitled Task'),
                            'status': t_metadata.get('status', 'todo'),
                            'priority': t_metadata.get('priority', ''),
                            'due_date': t_metadata.get('due_date', ''),
                            'project_id': p_id,
                            'epic_id': e_id,
                        })

                    subtasks_dir = os.path.join(tasks_dir, t_id, 'subtasks')
                    if not os.path.exists(subtasks_dir):
                        continue

                    for s_filename in os.listdir(subtasks_dir):
                        if not s_filename.endswith('.md'):
                            continue
                        s_id = s_filename[:-3]
                        s_metadata, _ = load_subtask(p_id, e_id, t_id, s_id, metadata_only=True)
                        if s_metadata:
                            items.append({
                                'type': 'subtask',
                                'id': s_id,
                                'seq_id': s_metadata.get('seq_id', ''),
                                'title': s_metadata.get('title', 'Untitled Subtask'),
                                'status': s_metadata.get('status', 'todo'),
                                'priority': s_metadata.get('priority', ''),
                                'due_date': s_metadata.get('due_date', ''),
                                'project_id': p_id,
                                'epic_id': e_id,
                                'task_id': t_id,
                            })
    except OSError as e:
        logger.error(f"Error loading work items: {e}")

    cache.set(cache_key, items, 30)
    return items


def find_entity_in_project(project_id, entity_id):
    """Find a task or subtask by ID within a project.
    
    Returns a dict with 'type', 'epic_id', 'task_id' (if subtask), and path info,
    or None if not found.
    """
    epics_dir = safe_join_path('projects', project_id, 'epics')
    if not os.path.exists(epics_dir):
        return None
    
    try:
        for e_filename in os.listdir(epics_dir):
            if not e_filename.endswith('.md'):
                continue
            epic_id = e_filename[:-3]
            
            tasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks')
            if not os.path.exists(tasks_dir):
                continue
            
            for t_filename in os.listdir(tasks_dir):
                if not t_filename.endswith('.md'):
                    continue
                task_id = t_filename[:-3]
                
                # Check if this is the task we're looking for
                if task_id == entity_id:
                    return {
                        'type': 'task',
                        'epic_id': epic_id,
                        'task_id': task_id
                    }
                
                # Check subtasks
                subtasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                if os.path.exists(subtasks_dir):
                    for s_filename in os.listdir(subtasks_dir):
                        if not s_filename.endswith('.md'):
                            continue
                        subtask_id = s_filename[:-3]
                        if subtask_id == entity_id:
                            return {
                                'type': 'subtask',
                                'epic_id': epic_id,
                                'task_id': task_id,
                                'subtask_id': subtask_id
                            }
    except OSError as e:
        logger.error(f"Error finding entity {entity_id}: {e}")
    
    return None


def update_reciprocal_dependency(project_id, source_id, target_id, relationship, action):
    """Update the reciprocal dependency on the target entity.
    
    Args:
        project_id: The project containing both entities
        source_id: The ID of the entity making the change
        target_id: The ID of the entity to update
        relationship: 'blocks' or 'blocked_by' (from source's perspective)
        action: 'add' or 'remove'
    
    The reciprocal relationship is:
        - If source 'blocks' target, then target should be 'blocked_by' source
        - If source is 'blocked_by' target, then target 'blocks' source
    """
    target_info = find_entity_in_project(project_id, target_id)
    if not target_info:
        logger.warning(f"Could not find target entity {target_id} for reciprocal dependency")
        return
    
    # Determine the reciprocal relationship
    reciprocal = 'blocked_by' if relationship == 'blocks' else 'blocks'
    
    # Load and update the target entity
    if target_info['type'] == 'task':
        metadata, content = load_task(project_id, target_info['epic_id'], target_info['task_id'])
        if metadata is None:
            return
        
        if reciprocal not in metadata:
            metadata[reciprocal] = []
        
        if action == 'add':
            if source_id not in metadata[reciprocal]:
                metadata[reciprocal].append(source_id)
        elif action == 'remove':
            metadata[reciprocal] = [x for x in metadata[reciprocal] if x != source_id]
        
        save_task(project_id, target_info['epic_id'], target_info['task_id'], metadata, content)
    
    elif target_info['type'] == 'subtask':
        metadata, content = load_subtask(project_id, target_info['epic_id'], 
                                          target_info['task_id'], target_info['subtask_id'])
        if metadata is None:
            return
        
        if reciprocal not in metadata:
            metadata[reciprocal] = []
        
        if action == 'add':
            if source_id not in metadata[reciprocal]:
                metadata[reciprocal].append(source_id)
        elif action == 'remove':
            metadata[reciprocal] = [x for x in metadata[reciprocal] if x != source_id]
        
        save_subtask(project_id, target_info['epic_id'], target_info['task_id'], 
                     target_info['subtask_id'], metadata, content)


def get_project_tasks_for_dependencies(project_id, exclude_task_id=None, exclude_subtask_id=None):
    """Get all tasks and subtasks in a project for dependency selection."""
    tasks_list = []
    epics_dir = safe_join_path('projects', project_id, 'epics')
    
    if not os.path.exists(epics_dir):
        return tasks_list
    
    # Cache epic titles
    epic_titles = {}
    
    try:
        for e_filename in os.listdir(epics_dir):
            if not e_filename.endswith('.md'):
                continue
            epic_id = e_filename[:-3]
            
            # Load epic metadata for title
            e_meta, _ = load_epic(project_id, epic_id, metadata_only=True)
            if e_meta:
                epic_titles[epic_id] = e_meta.get('title', 'Untitled Epic')
                epic_seq = e_meta.get('seq_id', '')
            else:
                epic_titles[epic_id] = 'Untitled Epic'
                epic_seq = ''
            
            tasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks')
            if not os.path.exists(tasks_dir):
                continue
            
            for t_filename in os.listdir(tasks_dir):
                if not t_filename.endswith('.md'):
                    continue
                task_id = t_filename[:-3]
                if exclude_task_id and task_id == exclude_task_id:
                    continue
                
                t_meta, _ = load_task(project_id, epic_id, task_id, metadata_only=True)
                if t_meta:
                    tasks_list.append({
                        'type': 'task',
                        'id': task_id,
                        'epic_id': epic_id,
                        'epic_title': epic_titles.get(epic_id, 'Untitled Epic'),
                        'seq_id': t_meta.get('seq_id', ''),
                        'title': t_meta.get('title', 'Untitled Task'),
                        'status': t_meta.get('status', 'todo'),
                        'priority': t_meta.get('priority', '')
                    })
                    
                    task_title = t_meta.get('title', 'Untitled Task')
                    task_seq = t_meta.get('seq_id', '')
                
                    subtasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                    if os.path.exists(subtasks_dir):
                        for s_filename in os.listdir(subtasks_dir):
                            if not s_filename.endswith('.md'):
                                continue
                            subtask_id = s_filename[:-3]
                            if exclude_subtask_id and subtask_id == exclude_subtask_id:
                                continue
                            
                            s_meta, _ = load_subtask(project_id, epic_id, task_id, subtask_id, metadata_only=True)
                            if s_meta:
                                tasks_list.append({
                                    'type': 'subtask',
                                    'id': subtask_id,
                                    'seq_id': s_meta.get('seq_id', ''),
                                    'task_id': task_id,
                                    'task_title': task_title,
                                    'task_seq_id': task_seq,
                                    'epic_id': epic_id,
                                    'epic_title': epic_titles.get(epic_id, 'Untitled Epic'),
                                    'title': s_meta.get('title', 'Untitled Subtask'),
                                    'status': s_meta.get('status', 'todo'),
                                    'priority': s_meta.get('priority', '')
                                })
    except OSError as e:
        logger.error(f"Error loading tasks for dependencies: {e}")
    
    # Sort by seq_id
    tasks_list.sort(key=lambda x: (x.get('seq_id', 'z999'), x.get('title', '')))
    
    return tasks_list


def get_project_activity(project_id):
    """Get recent activity (updates and system messages) across epics, tasks and subtasks in a project."""
    cache_key = f"activity:{project_id}:v2"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    # Use metadata_only=True for all loads in activity to speed up

    activity = []
    epics_dir = safe_join_path('projects', project_id, 'epics')

    if not os.path.exists(epics_dir):
        return activity

    for e_filename in os.listdir(epics_dir):
        if not e_filename.endswith('.md'):
            continue
        epic_id = e_filename[:-3]
        e_meta, _ = load_epic(project_id, epic_id, metadata_only=True)
        if e_meta:
            # Include epic updates/activity
            for u in e_meta.get('updates', []):
                ts = u.get('timestamp')
                try:
                    ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
                except ValueError:
                    ts_dt = ts
                activity.append({
                    'type': 'epic',
                    'entity_type': 'epic',
                    'title': e_meta.get('title', 'Untitled Epic'),
                    'content': u.get('content', ''),
                    'update_type': u.get('type', 'user'),  # Default to 'user' for backwards compatibility
                    'timestamp': ts_dt,
                    'url': reverse('epic_detail', kwargs={'project': project_id, 'epic': epic_id})
                })
        
        tasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks')
        if not os.path.exists(tasks_dir):
            continue

        for t_filename in os.listdir(tasks_dir):
            if not t_filename.endswith('.md'):
                continue
            task_id = t_filename[:-3]
            t_meta, _ = load_task(project_id, epic_id, task_id, metadata_only=True)
            if t_meta:
                for u in t_meta.get('updates', []):
                    ts = u.get('timestamp')
                    try:
                        ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
                    except ValueError:
                        ts_dt = ts
                    activity.append({
                        'type': 'task',
                        'entity_type': 'task',
                        'title': t_meta.get('title', 'Untitled Task'),
                        'content': u.get('content', ''),
                        'update_type': u.get('type', 'user'),  # Default to 'user' for backwards compatibility
                        'timestamp': ts_dt,
                        'url': reverse('task_detail', kwargs={'project': project_id, 'epic': epic_id, 'task': task_id})
                    })

            subtasks_dir = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
            if not os.path.exists(subtasks_dir):
                continue
            for s_filename in os.listdir(subtasks_dir):
                if not s_filename.endswith('.md'):
                    continue
                subtask_id = s_filename[:-3]
                s_meta, _ = load_subtask(project_id, epic_id, task_id, subtask_id, metadata_only=True)
                if s_meta:
                    for u in s_meta.get('updates', []):
                        ts = u.get('timestamp')
                        try:
                            ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
                        except ValueError:
                            ts_dt = ts
                        activity.append({
                            'type': 'subtask',
                            'entity_type': 'subtask',
                            'title': s_meta.get('title', 'Untitled Subtask'),
                            'content': u.get('content', ''),
                            'update_type': u.get('type', 'user'),  # Default to 'user' for backwards compatibility
                            'timestamp': ts_dt,
                            'url': reverse('subtask_detail', kwargs={'project': project_id, 'epic': epic_id, 'task': task_id, 'subtask': subtask_id})
                        })

    activity.sort(key=lambda x: x['timestamp'] if isinstance(x['timestamp'], datetime) else str(x['timestamp']), reverse=True)
    activity = activity[:50]  # Increased limit to show more activity
    cache.set(cache_key, activity, 30)
    return activity


def calendar_view(request):
    """Redirect to today day view."""
    today = date.today().strftime('%Y-%m-%d')
    return redirect('calendar_day', date_str=today)


def calendar_day(request, date_str):
    """Display a full day calendar view (24h grid)."""
    try:
        current_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        raise Http404("Invalid date format")
    
    # Get preferences from cookies or use defaults
    timeframe = int(request.COOKIES.get('calendar_timeframe', '12'))  # Default 12 hours
    start_hour = int(request.COOKIES.get('calendar_start_hour', '8'))  # Default 8am
    
    # Get project filter from cookies (comma-separated list of project IDs, or 'all')
    project_filter = request.COOKIES.get('calendar_project_filter', 'all')
    if project_filter == 'all':
        selected_projects = None  # Show all projects
    else:
        selected_projects = set(project_filter.split(',')) if project_filter else None
    
    # Handle form submission to update preferences
    if request.method == 'POST':
        if 'timeframe' in request.POST:
            timeframe = int(request.POST.get('timeframe', 12))
        if 'start_hour' in request.POST:
            start_hour = int(request.POST.get('start_hour', 8))
        if 'project_filter' in request.POST:
            project_filter_list = request.POST.getlist('project_filter')
            if 'all' in project_filter_list or len(project_filter_list) == 0:
                project_filter = 'all'
                selected_projects = None
            else:
                # Remove 'all' if present
                project_filter_list = [p for p in project_filter_list if p != 'all']
                project_filter = ','.join(project_filter_list)
                selected_projects = set(project_filter_list) if project_filter_list else None
    
    # Calculate end hour based on timeframe
    end_hour = start_hour + timeframe
    if end_hour > 24:
        end_hour = 24
    
    # Generate hours list based on timeframe
    hours = list(range(start_hour, end_hour))
    
    # Load project hierarchy for sidebar
    projects_hierarchy = get_all_projects_hierarchy()
        
    all_tasks = get_all_scheduled_tasks()
    day_tasks = []
    
    for task in all_tasks:
        start_str = task.get('schedule_start', '')
        end_str = task.get('schedule_end', '')
        
        # Check if task falls on this day
        # We handle both YYYY-MM-DD and YYYY-MM-DDTHH:MM formats
        task_start = None
        task_end = None
        
        if start_str:
            try:
                task_start = datetime.strptime(start_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                try:
                    task_start = datetime.strptime(start_str, '%Y-%m-%d')
                except ValueError: pass

        if end_str:
            try:
                task_end = datetime.strptime(end_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                try:
                    task_end = datetime.strptime(end_str, '%Y-%m-%d')
                except ValueError: pass
        
        if task_start and task_start.date() <= current_date:
            if not task_end or task_end.date() >= current_date:
                # Add positioning info for the 24h grid
                task_start_hour = 0
                task_start_min = 0
                duration_hours = 24
                
                if task_start.date() == current_date:
                    task_start_hour = task_start.hour
                    task_start_min = task_start.minute
                
                if task_end and task_end.date() == current_date:
                    duration_hours = (task_end.hour + task_end.minute/60.0) - (task_start_hour + task_start_min/60.0)
                elif task_end and task_end.date() > current_date:
                    duration_hours = 24 - (task_start_hour + task_start_min/60.0)
                elif not task_end:
                    duration_hours = 1 # Default 1 hour if no end time
                
                # Only show tasks that fall within the visible timeframe
                task_end_hour = task_start_hour + duration_hours
                if task_end_hour >= start_hour and task_start_hour < end_hour:
                    # Calculate visible portion of task
                    visible_start_hour = max(task_start_hour, start_hour)
                    visible_end_hour = min(task_end_hour, end_hour)
                    
                    # Adjust top position relative to start_hour
                    if task_start_hour < start_hour:
                        # Task starts before visible range
                        task['top'] = 0
                        visible_duration = visible_end_hour - start_hour
                    else:
                        # Task starts within visible range
                        task['top'] = ((task_start_hour - start_hour) * 60 + task_start_min)
                        visible_duration = visible_end_hour - visible_start_hour
                    
                    task['height'] = max(30, visible_duration * 60) # Min 30px height
                    
                    # Ensure project color is set
                    if 'project_color' not in task:
                        p_metadata, _ = load_project(task['project_id'], metadata_only=True)
                        task['project_color'] = get_project_color(task['project_id'], p_metadata.get('color') if p_metadata else None)
                    if 'project_color_bg' not in task:
                        task['project_color_bg'] = hex_to_rgba(task['project_color'], 0.15)
                    
                    # Filter by selected projects
                    if selected_projects is None or task['project_id'] in selected_projects:
                        day_tasks.append(task)
                
    prev_day = (current_date - timedelta(days=1)).strftime('%Y-%m-%d')
    next_day = (current_date + timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Calculate current week for the link
    year, week, _ = current_date.isocalendar()
    
    # Convert selected_projects set to list for template
    selected_projects_list = list(selected_projects) if selected_projects else []
    
    response = render(request, 'pm/calendar_day.html', {
        'date': current_date,
        'tasks': day_tasks,
        'hours': hours,
        'prev_day': prev_day,
        'next_day': next_day,
        'year': year,
        'week': week,
        'timeframe': timeframe,
        'start_hour': start_hour,
        'all_hours': list(range(24)),
        'projects': projects_hierarchy,
        'project_filter': project_filter,
        'selected_projects': selected_projects_list
    })
    
    # Set cookies for preferences
    response.set_cookie('calendar_timeframe', timeframe, max_age=31536000)  # 1 year
    response.set_cookie('calendar_start_hour', start_hour, max_age=31536000)  # 1 year
    response.set_cookie('calendar_project_filter', project_filter, max_age=31536000)  # 1 year
    
    return response


def update_task_schedule(request):
    """AJAX endpoint to update a task or subtask schedule."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    try:
        project_id = request.POST.get('project_id')
        epic_id = request.POST.get('epic_id')
        task_id = request.POST.get('task_id')
        subtask_id = request.POST.get('subtask_id')  # Optional
        schedule_start = request.POST.get('schedule_start')
        schedule_end = request.POST.get('schedule_end')
        
        if not all([project_id, epic_id, task_id, schedule_start]):
            return JsonResponse({'error': 'Missing required parameters'}, status=400)
        
        # Validate IDs
        if not (is_valid_project_id(project_id) and 
                validate_id(epic_id, 'epic') and 
                validate_id(task_id, 'task')):
            return JsonResponse({'error': 'Invalid IDs'}, status=400)
        
        if subtask_id:
            # Update subtask
            if not validate_id(subtask_id, 'subtask'):
                return JsonResponse({'error': 'Invalid subtask ID'}, status=400)
            
            metadata, content = load_subtask(project_id, epic_id, task_id, subtask_id)
            if metadata is None:
                return JsonResponse({'error': 'Subtask not found'}, status=404)
            
            metadata['schedule_start'] = schedule_start
            if schedule_end:
                metadata['schedule_end'] = schedule_end
            else:
                # Default to 1 hour after start
                try:
                    start_dt = datetime.strptime(schedule_start, '%Y-%m-%dT%H:%M')
                    end_dt = start_dt + timedelta(hours=1)
                    metadata['schedule_end'] = end_dt.strftime('%Y-%m-%dT%H:%M')
                except ValueError:
                    pass
            
            save_subtask(project_id, epic_id, task_id, subtask_id, metadata, content)
        else:
            # Update task
            metadata, content = load_task(project_id, epic_id, task_id)
            if metadata is None:
                return JsonResponse({'error': 'Task not found'}, status=404)
            
            metadata['schedule_start'] = schedule_start
            if schedule_end:
                metadata['schedule_end'] = schedule_end
            else:
                # Default to 1 hour after start
                try:
                    start_dt = datetime.strptime(schedule_start, '%Y-%m-%dT%H:%M')
                    end_dt = start_dt + timedelta(hours=1)
                    metadata['schedule_end'] = end_dt.strftime('%Y-%m-%dT%H:%M')
                except ValueError:
                    pass
            
            save_task(project_id, epic_id, task_id, metadata, content)
        
        return JsonResponse({'success': True})
    except Exception as e:
        logger.error(f"Error updating schedule: {e}")
        return JsonResponse({'error': str(e)}, status=500)


def reorder_items(request):
    """AJAX endpoint to persist ordering for tasks or subtasks."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    item_type = request.POST.get('type')
    project_id = request.POST.get('project_id')
    epic_id = request.POST.get('epic_id')
    task_id = request.POST.get('task_id')
    order = request.POST.get('order', '')

    if not all([item_type, project_id, epic_id, order]):
        return JsonResponse({'error': 'Missing parameters'}, status=400)

    if not (is_valid_project_id(project_id) and validate_id(epic_id, 'epic')):
        return JsonResponse({'error': 'Invalid IDs'}, status=400)

    ids = [i for i in order.split(',') if i]

    try:
        if item_type == 'task':
            for idx, t_id in enumerate(ids):
                if not validate_id(t_id, 'task'):
                    continue
                meta, content = load_task(project_id, epic_id, t_id)
                if meta is None:
                    continue
                meta['order'] = idx
                save_task(project_id, epic_id, t_id, meta, content)
        elif item_type == 'subtask':
            if not task_id or not validate_id(task_id, 'task'):
                return JsonResponse({'error': 'Invalid task ID'}, status=400)
            for idx, s_id in enumerate(ids):
                if not validate_id(s_id, 'subtask'):
                    continue
                meta, content = load_subtask(project_id, epic_id, task_id, s_id)
                if meta is None:
                    continue
                meta['order'] = idx
                save_subtask(project_id, epic_id, task_id, s_id, meta, content)
        else:
            return JsonResponse({'error': 'Invalid type'}, status=400)
    except Exception as e:
        logger.error(f"Error reordering items: {e}")
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'success': True})


def update_task_status(request):
    """AJAX endpoint to update task/subtask status (for kanban drag-and-drop)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    item_type = request.POST.get('type')
    project_id = request.POST.get('project_id')
    epic_id = request.POST.get('epic_id')
    task_id = request.POST.get('task_id')
    subtask_id = request.POST.get('subtask_id')
    status = request.POST.get('status')

    if not all([item_type, project_id, epic_id, status]):
        return JsonResponse({'error': 'Missing parameters'}, status=400)

    if not (is_valid_project_id(project_id) and validate_id(epic_id, 'epic')):
        return JsonResponse({'error': 'Invalid IDs'}, status=400)

    if status not in ['todo', 'in_progress', 'done']:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    try:
        if item_type == 'task':
            if not validate_id(task_id, 'task'):
                return JsonResponse({'error': 'Invalid task ID'}, status=400)
            meta, content = load_task(project_id, epic_id, task_id)
            if meta is None:
                return JsonResponse({'error': 'Task not found'}, status=404)
            meta['status'] = status
            save_task(project_id, epic_id, task_id, meta, content)
        elif item_type == 'subtask':
            if not task_id or not validate_id(task_id, 'task') or not validate_id(subtask_id, 'subtask'):
                return JsonResponse({'error': 'Invalid IDs'}, status=400)
            meta, content = load_subtask(project_id, epic_id, task_id, subtask_id)
            if meta is None:
                return JsonResponse({'error': 'Subtask not found'}, status=404)
            meta['status'] = status
            save_subtask(project_id, epic_id, task_id, subtask_id, meta, content)
        else:
            return JsonResponse({'error': 'Invalid type'}, status=400)
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'success': True})


def bulk_update_items(request):
    """AJAX endpoint to bulk update tasks or subtasks."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    item_type = request.POST.get('type')  # 'task' or 'subtask'
    project_id = request.POST.get('project_id')
    epic_id = request.POST.get('epic_id')
    task_id = request.POST.get('task_id')  # Only needed for subtasks
    ids = request.POST.get('ids', '')  # Comma-separated item IDs
    action = request.POST.get('action')  # 'status', 'priority', 'delete'
    value = request.POST.get('value', '')  # New value for the action

    if not all([item_type, project_id, epic_id, ids, action]):
        return JsonResponse({'error': 'Missing parameters'}, status=400)

    if not (is_valid_project_id(project_id) and validate_id(epic_id, 'epic')):
        return JsonResponse({'error': 'Invalid IDs'}, status=400)

    item_ids = [i.strip() for i in ids.split(',') if i.strip()]
    if not item_ids:
        return JsonResponse({'error': 'No items selected'}, status=400)

    # Validate action and value
    valid_statuses = ['todo', 'in_progress', 'done']
    valid_priorities = ['', '1', '2', '3', '4', '5']
    
    if action == 'status' and value not in valid_statuses:
        return JsonResponse({'error': 'Invalid status'}, status=400)
    if action == 'priority' and value not in valid_priorities:
        return JsonResponse({'error': 'Invalid priority'}, status=400)
    if action not in ['status', 'priority', 'delete']:
        return JsonResponse({'error': 'Invalid action'}, status=400)

    updated = 0
    try:
        if item_type == 'task':
            for t_id in item_ids:
                if not validate_id(t_id, 'task'):
                    continue
                if action == 'delete':
                    # Delete task and its subtasks
                    task_path = safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', t_id)
                    if os.path.exists(task_path + '.md'):
                        os.remove(task_path + '.md')
                        updated += 1
                    # Remove subtasks directory if exists
                    subtasks_dir = os.path.join(task_path, 'subtasks')
                    if os.path.exists(subtasks_dir):
                        shutil.rmtree(subtasks_dir)
                    if os.path.exists(task_path):
                        os.rmdir(task_path)
                else:
                    meta, content = load_task(project_id, epic_id, t_id)
                    if meta is None:
                        continue
                    if action == 'status':
                        old_status = meta.get('status', 'todo')
                        if old_status != value:
                            meta['status'] = value
                            add_activity_entry(meta, 'status_changed', old_status, value)
                    elif action == 'priority':
                        old_priority = meta.get('priority', '')
                        if old_priority != value:
                            if value:
                                meta['priority'] = value
                            elif 'priority' in meta:
                                del meta['priority']
                            add_activity_entry(meta, 'priority_changed', old_priority, value)
                    save_task(project_id, epic_id, t_id, meta, content)
                    updated += 1
                    
        elif item_type == 'subtask':
            if not task_id or not validate_id(task_id, 'task'):
                return JsonResponse({'error': 'Invalid task ID'}, status=400)
            for s_id in item_ids:
                if not validate_id(s_id, 'subtask'):
                    continue
                if action == 'delete':
                    subtask_path = safe_join_path('projects', project_id, 'epics', epic_id, 
                                                   'tasks', task_id, 'subtasks', s_id + '.md')
                    if os.path.exists(subtask_path):
                        os.remove(subtask_path)
                        updated += 1
                else:
                    meta, content = load_subtask(project_id, epic_id, task_id, s_id)
                    if meta is None:
                        continue
                    if action == 'status':
                        old_status = meta.get('status', 'todo')
                        if old_status != value:
                            meta['status'] = value
                            add_activity_entry(meta, 'status_changed', old_status, value)
                    elif action == 'priority':
                        old_priority = meta.get('priority', '')
                        if old_priority != value:
                            if value:
                                meta['priority'] = value
                            elif 'priority' in meta:
                                del meta['priority']
                            add_activity_entry(meta, 'priority_changed', old_priority, value)
                    save_subtask(project_id, epic_id, task_id, s_id, meta, content)
                    updated += 1
        else:
            return JsonResponse({'error': 'Invalid type'}, status=400)
            
    except Exception as e:
        logger.error(f"Error in bulk update: {e}")
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'success': True, 'updated': updated})


def calendar_week(request, year, week):
    """Display a work-week calendar overview (Mon-Fri)."""
    # Calculate the dates for the given year and week
    # isocalendar week 1 is the week with the first Thursday
    start_of_week = date.fromisocalendar(year, week, 1) # Monday
    days = []
    for i in range(5): # Mon-Fri
        days.append(start_of_week + timedelta(days=i))
        
    all_tasks = get_all_scheduled_tasks()
    week_data = [] # List of (day, tasks)
    
    for day in days:
        day_tasks = []
        for task in all_tasks:
            start_str = task.get('schedule_start', '')
            end_str = task.get('schedule_end', '')
            
            task_start = None
            task_end = None
            
            if start_str:
                try: task_start = datetime.strptime(start_str, '%Y-%m-%dT%H:%M').date()
                except ValueError:
                    try: task_start = datetime.strptime(start_str, '%Y-%m-%d').date()
                    except ValueError: pass
            
            if end_str:
                try: task_end = datetime.strptime(end_str, '%Y-%m-%dT%H:%M').date()
                except ValueError:
                    try: task_end = datetime.strptime(end_str, '%Y-%m-%d').date()
                    except ValueError: pass
                    
            if task_start and task_start <= day:
                if not task_end or task_end >= day:
                    # Ensure project color is set
                    if 'project_color' not in task:
                        p_metadata, _ = load_project(task['project_id'], metadata_only=True)
                        task['project_color'] = get_project_color(task['project_id'], p_metadata.get('color') if p_metadata else None)
                    if 'project_color_bg' not in task:
                        task['project_color_bg'] = hex_to_rgba(task['project_color'], 0.15)
                    day_tasks.append(task)
        week_data.append({'day': day, 'tasks': day_tasks})
        
    prev_week_date = start_of_week - timedelta(weeks=1)
    next_week_date = start_of_week + timedelta(weeks=1)
    
    prev_year, prev_week, _ = prev_week_date.isocalendar()
    next_year, next_week, _ = next_week_date.isocalendar()
    
    return render(request, 'pm/calendar_week.html', {
        'week_data': week_data,
        'year': year,
        'week': week,
        'prev_year': prev_year,
        'prev_week': prev_week,
        'next_year': next_year,
        'next_week': next_week,
        'today': date.today().strftime('%Y-%m-%d')
    })


def my_work(request):
    """Basic My Work view: in progress, due soon, overdue."""
    items = get_all_work_items()
    projects = get_all_projects_hierarchy()
    today = date.today()
    due_soon_cutoff = today + timedelta(days=7)

    # Filters
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    project_filter = request.GET.get('project', '')
    epic_filter = request.GET.get('epic', '')
    due_filter = request.GET.get('due', '')

    if status_filter:
        items = [i for i in items if i.get('status') == status_filter]
    if priority_filter:
        items = [i for i in items if i.get('priority') == priority_filter]
    if project_filter:
        items = [i for i in items if i.get('project_id') == project_filter]
    if epic_filter:
        items = [i for i in items if i.get('epic_id') == epic_filter]

    open_items = []
    in_progress = []
    due_soon = []
    overdue = []

    for item in items:
        status = item.get('status')
        due = parse_date_safe(item.get('due_date', ''))

        if due_filter == 'overdue' and (not due or due >= today):
            continue
        if due_filter == 'due_soon' and (not due or not (today <= due <= due_soon_cutoff)):
            continue
        if due_filter == 'today' and (not due or due != today):
            continue
        if due_filter == 'none' and due:
            continue

        if status != 'done':
            open_items.append(item)
        if status == 'in_progress':
            in_progress.append(item)

        if due:
            if due < today and status != 'done':
                overdue.append(item)
            elif today <= due <= due_soon_cutoff and status != 'done':
                due_soon.append(item)

    return render(request, 'pm/my_work.html', {
        'open_items': open_items,
        'in_progress': in_progress,
        'due_soon': due_soon,
        'overdue': overdue,
        'projects': projects,
        'filters': {
            'status': status_filter,
            'priority': priority_filter,
            'project': project_filter,
            'epic': epic_filter,
            'due': due_filter,
        }
    })


def today_view(request):
    """Today and Backlog view."""
    items = get_all_work_items()
    today = date.today()

    today_items = []
    backlog = []

    for item in items:
        status = item.get('status')
        if status == 'done':
            continue

        due = parse_date_safe(item.get('due_date', ''))
        if due == today:
            today_items.append(item)
        elif not due:
            backlog.append(item)

    return render(request, 'pm/today.html', {
        'today_items': today_items,
        'backlog': backlog,
    })


def kanban_view(request, project=None, epic=None):
    """Kanban board view with drag-and-drop."""
    if project and epic:
        # Epic-specific kanban
        epic_metadata, _ = load_epic(project, epic, metadata_only=True)
        if epic_metadata is None:
            raise Http404("Epic not found")
        
        tasks_dir = safe_join_path('projects', project, 'epics', epic, 'tasks')
        items = []
        if os.path.exists(tasks_dir):
            for filename in os.listdir(tasks_dir):
                if filename.endswith('.md'):
                    task_id = filename[:-3]
                    t_meta, _ = load_task(project, epic, task_id, metadata_only=True)
                    if t_meta and not t_meta.get('archived', False):
                        items.append({
                            'type': 'task',
                            'id': task_id,
                            'title': t_meta.get('title', 'Untitled Task'),
                            'status': t_meta.get('status', 'todo'),
                            'priority': t_meta.get('priority', ''),
                            'project_id': project,
                            'epic_id': epic,
                        })
        
        project_metadata, _ = load_project(project, metadata_only=True)
        epic_title = epic_metadata.get('title', 'Untitled Epic')
        project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
        
        return render(request, 'pm/kanban.html', {
            'items': items,
            'project': project,
            'project_title': project_title,
            'epic': epic,
            'epic_title': epic_title,
            'scope': 'epic'
        })
    elif project:
        # Project-wide kanban
        project_metadata, _ = load_project(project, metadata_only=True)
        if project_metadata is None:
            raise Http404("Project not found")
        
        items = []
        epics_dir = safe_join_path('projects', project, 'epics')
        if os.path.exists(epics_dir):
            for e_filename in os.listdir(epics_dir):
                if not e_filename.endswith('.md'):
                    continue
                epic_id = e_filename[:-3]
                e_meta, _ = load_epic(project, epic_id, metadata_only=True)
                if e_meta and e_meta.get('archived', False):
                    continue
                
                tasks_dir = safe_join_path('projects', project, 'epics', epic_id, 'tasks')
                if os.path.exists(tasks_dir):
                    for t_filename in os.listdir(tasks_dir):
                        if t_filename.endswith('.md'):
                            task_id = t_filename[:-3]
                            t_meta, _ = load_task(project, epic_id, task_id, metadata_only=True)
                            if t_meta and not t_meta.get('archived', False):
                                items.append({
                                    'type': 'task',
                                    'id': task_id,
                                    'title': t_meta.get('title', 'Untitled Task'),
                                    'status': t_meta.get('status', 'todo'),
                                    'priority': t_meta.get('priority', ''),
                                    'project_id': project,
                                    'epic_id': epic_id,
                                    'epic_title': e_meta.get('title', 'Untitled Epic')
                                })
        
        project_title = project_metadata.get('title', 'Untitled Project')
        
        return render(request, 'pm/kanban.html', {
            'items': items,
            'project': project,
            'project_title': project_title,
            'scope': 'project'
        })
    else:
        # All projects kanban
        items = get_all_work_items()
        # Filter to only tasks/subtasks
        items = [i for i in items if i.get('status') != 'done' or request.GET.get('show_done') == 'true']
        
        return render(request, 'pm/kanban.html', {
            'items': items,
            'scope': 'all'
        })


def search_view(request):
    """Global search across projects, epics, tasks, subtasks, notes, and updates."""
    query = request.GET.get('q', '').strip()
    results = []
    seen = set()

    def get_match_snippet(text, needle):
        if not text:
            return ''
        needle_lower = needle.lower()
        for line in text.splitlines():
            if needle_lower in line.lower():
                return line.strip()
        return ''

    def highlight_snippet(snippet, needle):
        if not snippet:
            return ''
        escaped_snippet = escape(snippet)
        escaped_needle = escape(needle)
        if not escaped_needle:
            return escaped_snippet
        highlighted = re.sub(
            re.escape(escaped_needle),
            r'<mark>\g<0></mark>',
            escaped_snippet,
            flags=re.IGNORECASE
        )
        return mark_safe(highlighted)

    if query:
        # Try SQLite FTS5 search first (faster)
        use_fts5 = True
        try:
            from .storage import IndexStorage
            index_storage = IndexStorage()
            fts_results = index_storage.search(query)
            
            if fts_results:
                for result in fts_results:
                    entity = result['entity']
                    key = (entity.type, entity.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    
                    # Determine URL based on type
                    if entity.type == 'project':
                        url = reverse('project_detail', kwargs={'project': entity.id})
                    elif entity.type == 'epic':
                        url = reverse('epic_detail', kwargs={'project': entity.project_id, 'epic': entity.id})
                    elif entity.type == 'task':
                        url = reverse('task_detail', kwargs={
                            'project': entity.project_id, 
                            'epic': entity.epic_id, 
                            'task': entity.id
                        })
                    elif entity.type == 'subtask':
                        url = reverse('subtask_detail', kwargs={
                            'project': entity.project_id,
                            'epic': entity.epic_id,
                            'task': entity.task_id,
                            'subtask': entity.id
                        })
                    elif entity.type == 'note':
                        url = reverse('note_detail', kwargs={'note_id': entity.id})
                    else:
                        continue
                    
                    # Determine snippet from match
                    snippet = ''
                    if result.get('title_match'):
                        snippet = f"Title: {result['title_match']}"
                    elif result.get('content_match'):
                        snippet = get_match_snippet(result['content_match'], query)
                    elif result.get('updates_match'):
                        snippet = get_match_snippet(result['updates_match'], query)
                    elif result.get('people_match'):
                        snippet = f"People: {result['people_match']}"
                    elif result.get('labels_match'):
                        snippet = f"Labels: {result['labels_match']}"
                    
                    # Get seq_id from metadata_json if available
                    seq_id = ''
                    try:
                        import json
                        meta = json.loads(entity.metadata_json)
                        seq_id = meta.get('seq_id', '')
                    except:
                        pass
                    
                    results.append({
                        'type': entity.type,
                        'title': entity.title,
                        'url': url,
                        'seq_id': seq_id,
                        'snippet': highlight_snippet(snippet, query)
                    })
        except Exception as e:
            logger.warning(f"FTS5 search failed, falling back to file search: {e}")
            use_fts5 = False
        
        # Skip fallback if FTS5 succeeded with results
        if use_fts5 and results:
            return render(request, 'pm/search.html', {
                'query': query,
                'results': results,
            })
        
        # Fallback to file-based search
        q = query.lower()
        projects_dir = safe_join_path('projects')

        if os.path.exists(projects_dir):
            project_ids = set()
            for entry in os.listdir(projects_dir):
                entry_path = os.path.join(projects_dir, entry)
                if entry.endswith('.md'):
                    candidate_id = entry[:-3]
                    if is_valid_project_id(candidate_id):
                        project_ids.add(candidate_id)
                elif os.path.isdir(entry_path):
                    if is_valid_project_id(entry):
                        project_ids.add(entry)

            for p_id in project_ids:
                p_meta, _ = load_project(p_id, metadata_only=True)
                if p_meta is None:
                    continue
                title_text = p_meta.get('title', '').lower()
                snippet = ''
                if q in title_text:
                    snippet = f"Title: {p_meta.get('title', 'Untitled Project')}"
                    key = ('project', p_id)
                    if key not in seen:
                        seen.add(key)
                        results.append({
                        'type': 'project',
                        'title': p_meta.get('title', 'Untitled Project'),
                            'url': reverse('project_detail', kwargs={'project': p_id}),
                            'snippet': highlight_snippet(snippet, query)
                        })
                else:
                    _, p_content = load_project(p_id, metadata_only=False)
                    content_snippet = get_match_snippet(p_content or '', query)
                    if q in (p_content or '').lower():
                        key = ('project', p_id)
                        if key not in seen:
                            seen.add(key)
                            results.append({
                            'type': 'project',
                            'title': p_meta.get('title', 'Untitled Project'),
                                'url': reverse('project_detail', kwargs={'project': p_id}),
                                'snippet': highlight_snippet(content_snippet, query)
                            })

                epics_dir = os.path.join(projects_dir, p_id, 'epics')
                if not os.path.exists(epics_dir):
                    continue

                for e_id in os.listdir(epics_dir):
                    e_path = os.path.join(epics_dir, f'{e_id}.md')
                    if not os.path.isfile(e_path):
                        continue
                    e_meta, _ = load_epic(p_id, e_id, metadata_only=True)
                    if e_meta is None:
                        continue
                    e_title = e_meta.get('title', '').lower()
                    snippet = ''
                    if q in e_title:
                        snippet = f"Title: {e_meta.get('title', 'Untitled Epic')}"
                        key = ('epic', e_id)
                        if key not in seen:
                            seen.add(key)
                            results.append({
                                'type': 'epic',
                                'title': e_meta.get('title', 'Untitled Epic'),
                                'seq_id': e_meta.get('seq_id', ''),
                                'url': reverse('epic_detail', kwargs={'project': p_id, 'epic': e_id}),
                                'snippet': highlight_snippet(snippet, query)
                            })
                    else:
                        _, e_content = load_epic(p_id, e_id, metadata_only=False)
                        content_snippet = get_match_snippet(e_content or '', query)
                        if q in (e_content or '').lower():
                            key = ('epic', e_id)
                            if key not in seen:
                                seen.add(key)
                                results.append({
                                    'type': 'epic',
                                    'title': e_meta.get('title', 'Untitled Epic'),
                                    'seq_id': e_meta.get('seq_id', ''),
                                    'url': reverse('epic_detail', kwargs={'project': p_id, 'epic': e_id}),
                                    'snippet': highlight_snippet(content_snippet, query)
                                })

                    tasks_dir = os.path.join(epics_dir, e_id, 'tasks')
                    if not os.path.exists(tasks_dir):
                        continue

                    for t_filename in os.listdir(tasks_dir):
                        if not t_filename.endswith('.md'):
                            continue
                        t_id = t_filename[:-3]
                        t_meta, _ = load_task(p_id, e_id, t_id, metadata_only=True)
                        if t_meta is None:
                            continue
                        updates_text = ' '.join([u.get('content', '') for u in t_meta.get('updates', [])])
                        haystack = (t_meta.get('title', '') + ' ' + updates_text).lower()
                        snippet = ''
                        if q in haystack:
                            if q in t_meta.get('title', '').lower():
                                snippet = f"Title: {t_meta.get('title', 'Untitled Task')}"
                            else:
                                snippet = get_match_snippet(updates_text, query)
                            key = ('task', t_id)
                            if key not in seen:
                                seen.add(key)
                                results.append({
                                    'type': 'task',
                                    'title': t_meta.get('title', 'Untitled Task'),
                                    'seq_id': t_meta.get('seq_id', ''),
                                    'url': reverse('task_detail', kwargs={'project': p_id, 'epic': e_id, 'task': t_id}),
                                    'snippet': highlight_snippet(snippet, query)
                                })
                        else:
                            _, t_content = load_task(p_id, e_id, t_id, metadata_only=False)
                            content_snippet = get_match_snippet(t_content or '', query)
                            if q in (t_content or '').lower():
                                key = ('task', t_id)
                                if key not in seen:
                                    seen.add(key)
                                    results.append({
                                        'type': 'task',
                                        'title': t_meta.get('title', 'Untitled Task'),
                                        'seq_id': t_meta.get('seq_id', ''),
                                        'url': reverse('task_detail', kwargs={'project': p_id, 'epic': e_id, 'task': t_id}),
                                        'snippet': highlight_snippet(content_snippet, query)
                                    })

                        subtasks_dir = os.path.join(tasks_dir, t_id, 'subtasks')
                        if not os.path.exists(subtasks_dir):
                            continue

                        for s_filename in os.listdir(subtasks_dir):
                            if not s_filename.endswith('.md'):
                                continue
                            s_id = s_filename[:-3]
                            s_meta, _ = load_subtask(p_id, e_id, t_id, s_id, metadata_only=True)
                            if s_meta is None:
                                continue
                            s_updates_text = ' '.join([u.get('content', '') for u in s_meta.get('updates', [])])
                            s_haystack = (s_meta.get('title', '') + ' ' + s_updates_text).lower()
                            snippet = ''
                            if q in s_haystack:
                                if q in s_meta.get('title', '').lower():
                                    snippet = f"Title: {s_meta.get('title', 'Untitled Subtask')}"
                                else:
                                    snippet = get_match_snippet(s_updates_text, query)
                                key = ('subtask', s_id)
                                if key not in seen:
                                    seen.add(key)
                                    results.append({
                                    'type': 'subtask',
                                    'seq_id': s_meta.get('seq_id', ''),
                                    'title': s_meta.get('title', 'Untitled Subtask'),
                                        'url': reverse('subtask_detail', kwargs={'project': p_id, 'epic': e_id, 'task': t_id, 'subtask': s_id}),
                                        'snippet': highlight_snippet(snippet, query)
                                    })
                            else:
                                _, s_content = load_subtask(p_id, e_id, t_id, s_id, metadata_only=False)
                                content_snippet = get_match_snippet(s_content or '', query)
                                if q in (s_content or '').lower():
                                    key = ('subtask', s_id)
                                    if key not in seen:
                                        seen.add(key)
                                        results.append({
                                        'type': 'subtask',
                                        'seq_id': s_meta.get('seq_id', ''),
                                        'title': s_meta.get('title', 'Untitled Subtask'),
                                            'url': reverse('subtask_detail', kwargs={'project': p_id, 'epic': e_id, 'task': t_id, 'subtask': s_id}),
                                            'snippet': highlight_snippet(content_snippet, query)
                                        })

        # Search notes
        notes_dir = safe_join_path('notes')
        if os.path.exists(notes_dir):
            try:
                filenames = [f for f in os.listdir(notes_dir) if f.endswith('.md')]
                for filename in filenames:
                    note_id = filename[:-3]
                    n_meta, n_content = load_note(note_id)
                    if n_meta is None:
                        continue
                    
                    key = ('note', note_id)
                    if key in seen:
                        continue
                    
                    snippet = ''
                    matched = False
                    
                    # Search in title
                    title_text = n_meta.get('title', '').lower()
                    if q in title_text:
                        snippet = f"Title: {n_meta.get('title', 'Untitled Note')}"
                        matched = True
                    
                    # Search in content
                    if not matched and n_content:
                        content_lower = n_content.lower()
                        if q in content_lower:
                            snippet = get_match_snippet(n_content, query)
                            matched = True
                    
                    # Search in people tags
                    if not matched:
                        people_tags = n_meta.get('people', [])
                        matching_people = [p for p in people_tags if q in p.lower()]
                        if matching_people:
                            snippet = f"People: {', '.join(matching_people)}"
                            matched = True
                    
                    # Search in labels
                    if not matched:
                        labels = n_meta.get('labels', [])
                        matching_labels = [l for l in labels if q in l.lower()]
                        if matching_labels:
                            snippet = f"Labels: {', '.join(matching_labels)}"
                            matched = True
                    
                    if matched:
                        seen.add(key)
                        results.append({
                            'type': 'note',
                            'title': n_meta.get('title', 'Untitled Note'),
                            'url': reverse('note_detail', kwargs={'note_id': note_id}),
                            'snippet': highlight_snippet(snippet, query)
                        })
            except OSError as e:
                logger.error(f"Error searching notes: {e}")

    return render(request, 'pm/search.html', {
        'query': query,
        'results': results,
    })


def notes_list(request):
    """Display list of all notes."""
    notes_dir = safe_join_path('notes')
    os.makedirs(notes_dir, exist_ok=True)
    
    notes = []
    try:
        filenames = [f for f in os.listdir(notes_dir) if f.endswith('.md')]
        for filename in sorted(filenames, reverse=True):
            note_id = filename[:-3]
            metadata, content = load_note(note_id)
            if metadata is not None:
                # Convert people names to objects with IDs
                people_list = normalize_people(metadata.get('people', []))
                people_with_ids = []
                for p_name in people_list:
                    # Check if this is actually a person ID (person- (7) + 8 hex = 15 chars)
                    if p_name.startswith('person-') and len(p_name) == 15:
                        # This is a person ID, not a name - load the person to get the actual name
                        person_id = p_name
                        person_meta, _ = load_person(person_id, metadata_only=True)
                        if person_meta:
                            actual_name = person_meta.get('name', '').strip()
                            if actual_name and actual_name != person_id:
                                p_name = actual_name
                            else:
                                # Person file exists but has no valid name, skip it
                                continue
                        else:
                            # Person ID does not exist, skip it
                            continue
                    else:
                        # This is a name, find the person ID
                        person_id = find_person_by_name(p_name)
                        if person_id:
                            # Load person to get their actual name (in case it changed)
                            person_meta, _ = load_person(person_id, metadata_only=True)
                            if person_meta:
                                actual_name = person_meta.get('name', '').strip()
                                if actual_name and actual_name != person_id:
                                    p_name = actual_name
                    
                    people_with_ids.append({
                        'name': p_name,
                        'id': person_id if person_id else None
                    })
                
                notes.append({
                    'id': note_id,
                    'title': metadata.get('title', 'Untitled Note'),
                    'created': metadata.get('created', ''),
                    'updated': metadata.get('updated', ''),
                    'people': people_with_ids,
                    'labels': metadata.get('labels', []),
                    'preview': content[:200] if content else ''
                })
    except OSError as e:
        logger.error(f"Error reading notes directory: {e}")
    
    return render(request, 'pm/notes_list.html', {
        'notes': notes
    })


def find_person_references(person_id):
    """Find all references to a person across all entity types by person_id."""
    references = {
        'projects': [],
        'epics': [],
        'tasks': [],
        'subtasks': [],
        'notes': []
    }
    
    # Load person to get their name (entities still reference by name)
    person_meta, _ = load_person(person_id, metadata_only=True)
    if not person_meta:
        return references
    
    person_name = person_meta.get('name', '')
    person_normalized = person_name.strip().lstrip('@')
    
    # Scan projects
    projects_dir = safe_join_path('projects')
    if os.path.exists(projects_dir):
        try:
            for p_id in os.listdir(projects_dir):
                p_dir = os.path.join(projects_dir, p_id)
                if not os.path.isdir(p_dir):
                    continue
                p_meta, _ = load_project(p_id, metadata_only=True)
                if p_meta:
                    people = normalize_people(p_meta.get('people', []))
                    if person_normalized in people:
                        references['projects'].append({
                            'id': p_id,
                            'title': p_meta.get('title', 'Untitled Project'),
                            'url': reverse('project_detail', kwargs={'project': p_id})
                        })
                
                # Scan epics
                epics_dir = os.path.join(p_dir, 'epics')
                if os.path.exists(epics_dir):
                    for e_file in os.listdir(epics_dir):
                        if not e_file.endswith('.md'):
                            continue
                        epic_id = e_file[:-3]
                        e_meta, _ = load_epic(p_id, epic_id, metadata_only=True)
                        if e_meta:
                            people = normalize_people(e_meta.get('people', []))
                            if person_normalized in people:
                                references['epics'].append({
                                    'id': epic_id,
                                    'seq_id': e_meta.get('seq_id', ''),
                                    'title': e_meta.get('title', 'Untitled Epic'),
                                    'project_id': p_id,
                                    'project_title': p_meta.get('title', 'Untitled Project') if p_meta else 'Untitled Project',
                                    'url': reverse('epic_detail', kwargs={'project': p_id, 'epic': epic_id})
                                })
                        
                        # Scan tasks
                        tasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks')
                        if os.path.exists(tasks_dir):
                            for t_file in os.listdir(tasks_dir):
                                if not t_file.endswith('.md'):
                                    continue
                                task_id = t_file[:-3]
                                t_meta, _ = load_task(p_id, epic_id, task_id, metadata_only=True)
                                if t_meta:
                                    people = normalize_people(t_meta.get('people', []))
                                    if person_normalized in people:
                                        references['tasks'].append({
                                            'id': task_id,
                                            'seq_id': t_meta.get('seq_id', ''),
                                            'title': t_meta.get('title', 'Untitled Task'),
                                            'project_id': p_id,
                                            'epic_id': epic_id,
                                            'epic_title': e_meta.get('title', 'Untitled Epic') if e_meta else 'Untitled Epic',
                                            'project_title': p_meta.get('title', 'Untitled Project') if p_meta else 'Untitled Project',
                                            'url': reverse('task_detail', kwargs={'project': p_id, 'epic': epic_id, 'task': task_id})
                                        })
                                    
                                    # Scan subtasks
                                    subtasks_dir = safe_join_path('projects', p_id, 'epics', epic_id, 'tasks', task_id, 'subtasks')
                                    if os.path.exists(subtasks_dir):
                                        for s_file in os.listdir(subtasks_dir):
                                            if not s_file.endswith('.md'):
                                                continue
                                            subtask_id = s_file[:-3]
                                            s_meta, _ = load_subtask(p_id, epic_id, task_id, subtask_id, metadata_only=True)
                                            if s_meta:
                                                people = normalize_people(s_meta.get('people', []))
                                                if person_normalized in people:
                                                    references['subtasks'].append({
                                                        'id': subtask_id,
                                                        'seq_id': s_meta.get('seq_id', ''),
                                                        'title': s_meta.get('title', 'Untitled Subtask'),
                                                        'project_id': p_id,
                                                        'epic_id': epic_id,
                                                        'task_id': task_id,
                                                        'task_title': t_meta.get('title', 'Untitled Task'),
                                                        'epic_title': e_meta.get('title', 'Untitled Epic') if e_meta else 'Untitled Epic',
                                                        'project_title': p_meta.get('title', 'Untitled Project') if p_meta else 'Untitled Project',
                                                        'url': reverse('subtask_detail', kwargs={'project': p_id, 'epic': epic_id, 'task': task_id, 'subtask': subtask_id})
                                                    })
        except OSError as e:
            logger.error(f"Error scanning projects for person references: {e}")
    
    # Scan notes
    notes_dir = safe_join_path('notes')
    if os.path.exists(notes_dir):
        try:
            for n_file in os.listdir(notes_dir):
                if not n_file.endswith('.md'):
                    continue
                note_id = n_file[:-3]
                n_meta, _ = load_note(note_id)
                if n_meta:
                    people = normalize_people(n_meta.get('people', []))
                    if person_normalized in people:
                        references['notes'].append({
                            'id': note_id,
                            'title': n_meta.get('title', 'Untitled Note'),
                            'created': n_meta.get('created', ''),
                            'updated': n_meta.get('updated', ''),
                            'url': reverse('note_detail', kwargs={'note_id': note_id})
                        })
        except OSError as e:
            logger.error(f"Error scanning notes for person references: {e}")
    
    return references


def people_list(request):
    """Display list of all people with their reference counts."""
    # Handle creating a new person
    if request.method == 'POST' and 'create_person' in request.POST:
        person_name = request.POST.get('person_name', '').strip().lstrip('@')
        if person_name:
            # Generate unique person ID
            person_id = f'person-{uuid.uuid4().hex[:8]}'
            # Create person file with metadata
            metadata = {
                'name': person_name,
                'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            }
            save_person(person_id, metadata)
            # Invalidate cache
            cache.delete("all_people:v3")
            return redirect('people_list')
    
    # Get all people IDs from the system
    all_people_ids = get_all_people_in_system()
    
    # Build people dict with reference counts
    people_list_data = []
    for person_id in all_people_ids:
        person_meta, _ = load_person(person_id, metadata_only=True)
        if not person_meta:
            continue
        
        # Get person name - ensure we never use the ID as the name
        person_name = person_meta.get('name', '').strip()
        if not person_name or person_name == person_id:
            # If name is missing or same as ID, skip this person or use a placeholder
            person_name = 'Unknown'
        references = find_person_references(person_id)
        total_refs = (len(references['projects']) + len(references['epics']) + 
                     len(references['tasks']) + len(references['subtasks']) + 
                     len(references['notes']))
        
        people_list_data.append({
            'id': person_id,
            'name': person_name,
            'projects_count': len(references['projects']),
            'epics_count': len(references['epics']),
            'tasks_count': len(references['tasks']),
            'subtasks_count': len(references['subtasks']),
            'notes_count': len(references['notes']),
            'total_count': total_refs
        })
    
    # Sort by name
    people_list_sorted = sorted(people_list_data, key=lambda x: x['name'].lower())
    
    return render(request, 'pm/people_list.html', {
        'people': people_list_sorted
    })


def person_detail(request, person_id):
    """Display and edit person information and all references."""
    # Load person metadata
    person_metadata, person_content = load_person(person_id)
    if person_metadata is None:
        raise Http404("Person not found")
    
    edit_mode = request.GET.get('edit', 'false') == 'true'
    
    # Handle POST requests for editing
    if request.method == 'POST':
        new_name = request.POST.get('name', '').strip().lstrip('@')
        job_title = request.POST.get('job_title', '').strip()
        company = request.POST.get('company', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        
        # Update metadata (keep same person_id)
        person_metadata['name'] = new_name
        if job_title:
            person_metadata['job_title'] = job_title
        elif 'job_title' in person_metadata:
            del person_metadata['job_title']
            
        if company:
            person_metadata['company'] = company
        elif 'company' in person_metadata:
            del person_metadata['company']
            
        if email:
            person_metadata['email'] = email
        elif 'email' in person_metadata:
            del person_metadata['email']
            
        if phone:
            person_metadata['phone'] = phone
        elif 'phone' in person_metadata:
            del person_metadata['phone']
        
        # Ensure created date exists
        if 'created' not in person_metadata:
            person_metadata['created'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        
        # Save with same person_id (name can change, ID stays the same)
        save_person(person_id, person_metadata, person_content or '')
        # Invalidate cache
        cache.delete("all_people:v3")
        
        return redirect('person_detail', person_id=person_id)
    
    # Find all references
    references = find_person_references(person_id)
    
    # Calculate totals
    total_refs = (len(references['projects']) + len(references['epics']) + 
                 len(references['tasks']) + len(references['subtasks']) + 
                 len(references['notes']))
    
    # Get person name - ensure we never use the ID as the name
    person_name = person_metadata.get('name', '').strip()
    if not person_name or person_name == person_id:
        person_name = 'Unknown'
    
    return render(request, 'pm/person_detail.html', {
        'person_id': person_id,
        'person_name': person_name,
        'metadata': person_metadata,
        'content': person_content or '',
        'projects': references['projects'],
        'epics': references['epics'],
        'tasks': references['tasks'],
        'subtasks': references['subtasks'],
        'notes': references['notes'],
        'total_refs': total_refs,
        'edit_mode': edit_mode
    })


def load_note(note_id, metadata_only=False):
    """Load a note from disk."""
    # Basic validation - ensure note_id is safe filename
    if not note_id or '/' in note_id or '..' in note_id:
        return None, None
    note_path = safe_join_path('notes', f'{note_id}.md')
    return sync_manager.load_entity_with_index(
        note_path, note_id, 'note', 
        'Untitled Note', 'active', metadata_only=metadata_only
    )


def save_note(note_id, metadata, content):
    """Save a note to disk."""
    # Basic validation - ensure note_id is safe filename
    if not note_id or '/' in note_id or '..' in note_id:
        raise Http404("Invalid note ID")
    note_path = safe_join_path('notes', f'{note_id}.md')
    sync_manager.save_entity_with_sync(note_path, note_id, 'note', metadata, content)


def note_detail(request, note_id):
    """Display or edit a note."""
    metadata, content = load_note(note_id)
    if metadata is None:
        raise Http404("Note not found")
    
    edit_mode = request.GET.get('edit', 'false') == 'true'
    
    # Handle quick updates for linking entities
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        
        if quick_update == 'link_project':
            project_id = request.POST.get('project_id', '').strip()
            if project_id and is_valid_project_id(project_id):
                p_meta, p_content = load_project(project_id)
                if p_meta:
                    notes_list = p_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        p_meta['notes'] = notes_list
                        save_project(project_id, p_meta, p_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'unlink_project':
            project_id = request.POST.get('project_id', '').strip()
            if project_id and is_valid_project_id(project_id):
                p_meta, p_content = load_project(project_id)
                if p_meta:
                    notes_list = p_meta.get('notes', [])
                    p_meta['notes'] = [n for n in notes_list if n != note_id]
                    save_project(project_id, p_meta, p_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'link_epic':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            if project_id and epic_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic'):
                e_meta, e_content = load_epic(project_id, epic_id)
                if e_meta:
                    notes_list = e_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        e_meta['notes'] = notes_list
                        save_epic(project_id, epic_id, e_meta, e_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'unlink_epic':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            if project_id and epic_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic'):
                e_meta, e_content = load_epic(project_id, epic_id)
                if e_meta:
                    notes_list = e_meta.get('notes', [])
                    e_meta['notes'] = [n for n in notes_list if n != note_id]
                    save_epic(project_id, epic_id, e_meta, e_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'link_task':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            if project_id and epic_id and task_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task'):
                t_meta, t_content = load_task(project_id, epic_id, task_id)
                if t_meta:
                    notes_list = t_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        t_meta['notes'] = notes_list
                        save_task(project_id, epic_id, task_id, t_meta, t_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'unlink_task':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            if project_id and epic_id and task_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task'):
                t_meta, t_content = load_task(project_id, epic_id, task_id)
                if t_meta:
                    notes_list = t_meta.get('notes', [])
                    t_meta['notes'] = [n for n in notes_list if n != note_id]
                    save_task(project_id, epic_id, task_id, t_meta, t_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'link_subtask':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            subtask_id = request.POST.get('subtask_id', '').strip()
            if project_id and epic_id and task_id and subtask_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task') and validate_id(subtask_id, 'subtask'):
                s_meta, s_content = load_subtask(project_id, epic_id, task_id, subtask_id)
                if s_meta:
                    notes_list = s_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        s_meta['notes'] = notes_list
                        save_subtask(project_id, epic_id, task_id, subtask_id, s_meta, s_content)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'unlink_subtask':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            subtask_id = request.POST.get('subtask_id', '').strip()
            if project_id and epic_id and task_id and subtask_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task') and validate_id(subtask_id, 'subtask'):
                s_meta, s_content = load_subtask(project_id, epic_id, task_id, subtask_id)
                if s_meta:
                    notes_list = s_meta.get('notes', [])
                    s_meta['notes'] = [n for n in notes_list if n != note_id]
                    save_subtask(project_id, epic_id, task_id, subtask_id, s_meta, s_content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'add_label':
            label = request.POST.get('label', '').strip()
            if label:
                labels_list = normalize_labels(metadata.get('labels', []))
                if label not in labels_list:
                    labels_list.append(label)
                    metadata['labels'] = labels_list
                    save_note(note_id, metadata, content)
                    cache.delete("all_labels:v1")  # Invalidate cache
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'remove_label':
            label = request.POST.get('label', '').strip()
            if label:
                labels_list = normalize_labels(metadata.get('labels', []))
                labels_list = [l for l in labels_list if l != label]
                metadata['labels'] = labels_list
                save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'add_person':
            person = request.POST.get('person', '').strip()
            if person:
                people_list = normalize_people(metadata.get('people', []))
                if person not in people_list:
                    people_list.append(person)
                    metadata['people'] = people_list
                    save_note(note_id, metadata, content)
                    cache.delete("all_people:v1")  # Invalidate cache
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'remove_person':
            person = request.POST.get('person', '').strip()
            if person:
                people_list = normalize_people(metadata.get('people', []))
                people_list = [p for p in people_list if p != person]
                metadata['people'] = people_list
                save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'set_project':
            # Set or change the project for this note
            project_id = request.POST.get('project_id', '').strip()
            if project_id and is_valid_project_id(project_id):
                # Verify project exists
                p_meta, p_content = load_project(project_id)
                if p_meta:
                    # Link note to project
                    notes_list = p_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        p_meta['notes'] = notes_list
                        save_project(project_id, p_meta, p_content)
                    # Associate project with note
                    metadata['note_project_id'] = project_id
                    save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'create_project_from_note':
            # Create a new project and associate it with this note
            title = request.POST.get('title', 'New Project').strip()
            if title:
                project_id = f'project-{uuid.uuid4().hex[:8]}'
                color = get_project_color(project_id)
                new_metadata = {
                    'title': title,
                    'status': 'active',
                    'created': datetime.now().strftime('%Y-%m-%d'),
                    'color': color,
                    'notes': [note_id]  # Link note to project
                }
                save_project(project_id, new_metadata, '')
                # Associate this project with the note
                metadata['note_project_id'] = project_id
                save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'create_epic_from_note':
            # Create a new epic associated with the note project
            title = request.POST.get('title', 'New Epic').strip()
            project_id = metadata.get('note_project_id', '').strip()
            if title and project_id and validate_id(project_id, 'project'):
                # Verify project exists
                p_meta, _ = load_project(project_id, metadata_only=True)
                if p_meta:
                    epic_id = f'epic-{uuid.uuid4().hex[:8]}'
                    seq_id = get_next_seq_id(project_id, 'epic')
                    priority = request.POST.get('priority', '').strip() or '3'
                    epic_metadata = {
                        'title': title,
                        'status': 'active',
                        'seq_id': seq_id,
                        'priority': priority,
                        'created': datetime.now().strftime('%Y-%m-%d'),
                        'notes': [note_id]  # Link note to epic
                    }
                    save_epic(project_id, epic_id, epic_metadata, '')
                    # Track epics created in this note
                    note_epics = metadata.get('note_epics', [])
                    if epic_id not in note_epics:
                        note_epics.append(epic_id)
                    metadata['note_epics'] = note_epics
                    save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'create_task_from_note':
            # Create a new task in an epic (from note or existing)
            title = request.POST.get('title', 'New Task').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            project_id = metadata.get('note_project_id', '').strip()
            
            if title and epic_id and project_id and validate_id(project_id, 'project') and validate_id(epic_id, 'epic'):
                # Verify epic exists and belongs to project
                e_meta, _ = load_epic(project_id, epic_id, metadata_only=True)
                if e_meta:
                    task_id = f'task-{uuid.uuid4().hex[:8]}'
                    seq_id = get_next_seq_id(project_id, 'task')
                    priority = request.POST.get('priority', '').strip() or '3'
                    status = request.POST.get('status', 'todo')
                    task_metadata = {
                        'title': title,
                        'status': status,
                        'seq_id': seq_id,
                        'priority': priority,
                        'created': datetime.now().strftime('%Y-%m-%d'),
                        'notes': [note_id]  # Link note to task
                    }
                    save_task(project_id, epic_id, task_id, task_metadata, '')
                    # Track tasks created in this note
                    note_tasks = metadata.get('note_tasks', [])
                    if task_id not in note_tasks:
                        note_tasks.append(task_id)
                    metadata['note_tasks'] = note_tasks
                    save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
    
    if request.method == 'POST':
        metadata['title'] = request.POST.get('title', metadata.get('title', 'Untitled Note'))
        metadata['updated'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        
        labels = normalize_labels(request.POST.get('labels', ''))
        old_labels = set(normalize_labels(metadata.get('labels', [])))
        if labels:
            metadata['labels'] = labels
            # Invalidate cache if new labels were added
            if set(labels) - old_labels:
                cache.delete("all_labels:v1")
        else:
            metadata.pop('labels', None)
        
        people = normalize_people(request.POST.get('people', ''))
        old_people = set(normalize_people(metadata.get('people', [])))
        if people:
            metadata['people'] = people
            # Invalidate cache if new people were added
            if set(people) - old_people:
                cache.delete("all_people:v1")
        else:
            metadata.pop('people', None)
        
        content = request.POST.get('content', content)
        
        save_note(note_id, metadata, content)
        return redirect('note_detail', note_id=note_id)
    
    labels_list = normalize_labels(metadata.get('labels', []))
    labels_with_colors = [{'name': l, 'color': label_color(l)} for l in labels_list]
    labels_names = labels_list
    
    people_list = normalize_people(metadata.get('people', []))
    people_with_colors = []
    for p_name in people_list:
        # Check if this is actually a person ID (person- (7) + 8 hex = 15 chars)
        if p_name.startswith('person-') and len(p_name) == 15:
            # This is a person ID, not a name - load the person to get the actual name
            person_id = p_name
            person_meta, _ = load_person(person_id, metadata_only=True)
            if person_meta:
                actual_name = person_meta.get('name', '').strip()
                if actual_name and actual_name != person_id:
                    p_name = actual_name
                else:
                    # Person file exists but has no valid name, skip it
                    continue
            else:
                # Person ID does not exist, skip it
                continue
        else:
            # This is a name, find the person ID
            person_id = find_person_by_name(p_name)
            if person_id:
                # Load person to get their actual name (in case it changed)
                person_meta, _ = load_person(person_id, metadata_only=True)
                if person_meta:
                    actual_name = person_meta.get('name', '').strip()
                    if actual_name and actual_name != person_id:
                        p_name = actual_name
        
        people_with_colors.append({
            'name': p_name,
            'id': person_id if person_id else None,
            'color': label_color(p_name)
        })
    people_names = people_list
    
    # Get all labels and people for dropdowns (lazy-loaded, cached)
    all_labels = get_all_labels_in_system()
    all_people = get_all_people_names_in_system()  # Use names version for dropdowns
    
    # Get all entities for linking
    all_entities = get_all_entities_for_linking()
    
    # Find backlinks (entities that have linked this note)
    backlinks = find_note_backlinks(note_id)
    
    # Filter out already linked entities from dropdowns
    linked_project_ids = [p['id'] for p in backlinks['projects']]
    linked_epic_ids = [(e['project_id'], e['id']) for e in backlinks['epics']]
    linked_task_ids = [(t['project_id'], t['epic_id'], t['id']) for t in backlinks['tasks']]
    linked_subtask_ids = [(s['project_id'], s['epic_id'], s['task_id'], s['id']) for s in backlinks['subtasks']]
    
    available_projects = [p for p in all_entities['projects'] if p['id'] not in linked_project_ids]
    available_epics = [e for e in all_entities['epics'] if (e['project_id'], e['id']) not in linked_epic_ids]
    available_tasks = [t for t in all_entities['tasks'] if (t['project_id'], t['epic_id'], t['id']) not in linked_task_ids]
    available_subtasks = [s for s in all_entities['subtasks'] if (s['project_id'], s['epic_id'], s['task_id'], s['id']) not in linked_subtask_ids]
    
    # Get note associated project and epics/tasks created in this note
    note_project_id = metadata.get('note_project_id', '')
    note_project = None
    if note_project_id and is_valid_project_id(note_project_id):
        p_meta, _ = load_project(note_project_id, metadata_only=True)
        if p_meta:
            note_project = {
                'id': note_project_id,
                'title': p_meta.get('title', 'Untitled Project')
            }
    
    # Get epics created in this note
    note_epic_ids = metadata.get('note_epics', [])
    note_epics = []
    if note_project_id:
        for epic_id in note_epic_ids:
            if validate_id(epic_id, 'epic'):
                e_meta, _ = load_epic(note_project_id, epic_id, metadata_only=True)
                if e_meta:
                    note_epics.append({
                        'id': epic_id,
                        'title': e_meta.get('title', 'Untitled Epic'),
                        'seq_id': e_meta.get('seq_id', '')
                    })
    
    # Get all epics from the note project (for task creation dropdown)
    project_epics = []
    if note_project_id:
        epics_dir = safe_join_path('projects', note_project_id, 'epics')
        if os.path.exists(epics_dir):
            try:
                for e_file in os.listdir(epics_dir):
                    if not e_file.endswith('.md'):
                        continue
                    epic_id = e_file[:-3]
                    e_meta, _ = load_epic(note_project_id, epic_id, metadata_only=True)
                    if e_meta:
                        project_epics.append({
                            'id': epic_id,
                            'title': e_meta.get('title', 'Untitled Epic'),
                            'seq_id': e_meta.get('seq_id', '')
                        })
            except OSError:
                pass
        project_epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
    
    # Get tasks created in this note
    note_task_ids = metadata.get('note_tasks', [])
    note_tasks = []
    if note_project_id:
        for task_id in note_task_ids:
            if validate_id(task_id, 'task'):
                # Find which epic this task belongs to
                for epic in project_epics:
                    tasks_dir = safe_join_path('projects', note_project_id, 'epics', epic['id'], 'tasks')
                    if os.path.exists(tasks_dir):
                        for t_file in os.listdir(tasks_dir):
                            if t_file[:-3] == task_id:
                                t_meta, _ = load_task(note_project_id, epic['id'], task_id, metadata_only=True)
                                if t_meta:
                                    note_tasks.append({
                                        'id': task_id,
                                        'epic_id': epic['id'],
                                        'epic_title': epic['title'],
                                        'title': t_meta.get('title', 'Untitled Task'),
                                        'seq_id': t_meta.get('seq_id', '')
                                    })
                                break
    
    return render(request, 'pm/note_detail.html', {
        'metadata': metadata,
        'content': content,
        'note_id': note_id,
        'labels': labels_with_colors,
        'labels_names': labels_names,
        'people': people_with_colors,
        'people_names': people_names,
        'all_labels': all_labels,
        'all_people': all_people,  # This is now get_all_people_names_in_system() result
        'edit_mode': edit_mode,
        'backlinks': backlinks,
        'available_projects': available_projects,
        'available_epics': available_epics,
        'available_tasks': available_tasks,
        'available_subtasks': available_subtasks,
        'note_project': note_project,
        'note_epics': note_epics,
        'project_epics': project_epics,
        'note_tasks': note_tasks
    })


def new_note(request):
    """Create a new note."""
    if request.method == 'POST':
        title = request.POST.get('title', 'New Note')
        content = request.POST.get('content', '')
        labels = normalize_labels(request.POST.get('labels', ''))
        people = normalize_people(request.POST.get('people', ''))
        
        note_id = f'note-{uuid.uuid4().hex[:8]}'
        metadata = {
            'title': title,
            'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'updated': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        if labels:
            metadata['labels'] = labels
        if people:
            metadata['people'] = people
        
        save_note(note_id, metadata, content)
        return redirect('note_detail', note_id=note_id)
    
    return render(request, 'pm/new_note.html')


def delete_note(request, note_id):
    """Delete a note."""
    if request.method == 'POST':
        # Basic validation
        if not note_id or '/' in note_id or '..' in note_id:
            raise Http404("Invalid note ID")
        note_path = safe_join_path('notes', f'{note_id}.md')
        if os.path.exists(note_path):
            os.remove(note_path)
        return redirect('notes_list')
    raise Http404("Invalid request")


def whois_query(request):
    """AJAX endpoint to query whois database."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    query = request.POST.get('query', '').strip()
    if not query:
        return JsonResponse({'error': 'Query is required'}, status=400)
    
    # Sanitize input - only allow alphanumeric, dots, dashes, colons (for IPv6)
    if not re.match(r'^[a-zA-Z0-9.\-:]+$', query):
        return JsonResponse({'error': 'Invalid query format'}, status=400)
    
    try:
        # Run whois command with timeout (30 seconds)
        # Use the same syntax as Debian/Ubuntu whois command
        result = subprocess.run(
            ['whois', query],
            capture_output=True,
            text=True,
            timeout=30,
            check=False  # Do not raise exception on non-zero exit
        )
        
        # whois command returns exit code 0 on success, but may also return 1 for some queries
        # We will return the output regardless of exit code
        output = result.stdout
        
        # If stdout is empty but stderr has content, check if it is an error or just info
        if not output and result.stderr:
            # Some whois servers send info to stderr, check if it is actually an error
            if 'error' in result.stderr.lower() or 'not found' in result.stderr.lower():
                return JsonResponse({
                    'success': False,
                    'error': result.stderr.strip()
                })
            # Otherwise use stderr as output (some whois implementations do this)
            output = result.stderr
        
        if not output:
            return JsonResponse({
                'success': False,
                'error': 'No results returned from whois database'
            })
        
        return JsonResponse({
            'success': True,
            'output': output.strip()
        })
        
    except subprocess.TimeoutExpired:
        return JsonResponse({
            'success': False,
            'error': 'Whois query timed out after 30 seconds'
        })
    except FileNotFoundError:
        return JsonResponse({
            'success': False,
            'error': 'Whois command not found. Please install whois package (apt-get install whois)'
        })
    except Exception as e:
        logger.error(f"Error running whois query: {e}")
        return JsonResponse({
            'success': False,
            'error': f'Error executing whois command: {str(e)}'
        })


def dig_query(request):
    """AJAX endpoint to query DNS using dig command."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    query = request.POST.get('query', '').strip()
    if not query:
        return JsonResponse({'error': 'Query is required'}, status=400)
    
    # Sanitize input - allow alphanumeric, dots, dashes, colons (for IPv6), and @ for email-style queries
    if not re.match(r'^[a-zA-Z0-9.\-:@]+$', query):
        return JsonResponse({'error': 'Invalid query format'}, status=400)
    
    query_type = request.POST.get('type', '').strip()
    server = request.POST.get('server', '').strip()
    
    try:
        # Build dig command - same syntax as Debian/Ubuntu dig
        dig_cmd = ['dig']
        
        # Add server if specified (e.g., dig @8.8.8.8 example.com)
        if server:
            # Validate server format
            if not re.match(r'^[a-zA-Z0-9.\-:]+$', server):
                return JsonResponse({'error': 'Invalid server format'}, status=400)
            dig_cmd.extend(['@' + server])
        
        # Check if query is an IP address (for reverse DNS/PTR)
        is_ip = re.match(r'^(\d{1,3}\.){3}\d{1,3}$', query) or ':' in query
        
        # If IP address and no type specified, use -x for reverse DNS
        if is_ip and not query_type:
            dig_cmd.append('-x')
            dig_cmd.append(query)
        else:
            # Add query type if specified (e.g., dig example.com MX)
            if query_type:
                # Validate query type
                valid_types = ['A', 'AAAA', 'MX', 'NS', 'TXT', 'CNAME', 'SOA', 'PTR', 'ANY']
                if query_type not in valid_types:
                    return JsonResponse({'error': 'Invalid query type'}, status=400)
                dig_cmd.append(query_type)
            
            # Add the query (domain or IP)
            dig_cmd.append(query)
        
        # Add +noall +answer to get cleaner output (optional, but get full output like command line)
        # Actually, return the full dig output to match command line behavior
        
        # Run dig command with timeout (30 seconds)
        result = subprocess.run(
            dig_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False  # Do not raise exception on non-zero exit
        )
        
        # dig returns exit code 0 on success
        output = result.stdout
        
        # If stdout is empty, check stderr
        if not output and result.stderr:
            # Some dig errors go to stderr
            if 'error' in result.stderr.lower() or 'not found' in result.stderr.lower():
                return JsonResponse({
                    'success': False,
                    'error': result.stderr.strip()
                })
            # Otherwise use stderr as output
            output = result.stderr
        
        if not output:
            return JsonResponse({
                'success': False,
                'error': 'No results returned from dig command'
            })
        
        return JsonResponse({
            'success': True,
            'output': output.strip()
        })
        
    except subprocess.TimeoutExpired:
        return JsonResponse({
            'success': False,
            'error': 'Dig query timed out after 30 seconds'
        })
    except FileNotFoundError:
        return JsonResponse({
            'success': False,
            'error': 'Dig command not found. Please install dnsutils package (apt-get install dnsutils)'
        })
    except Exception as e:
        logger.error(f"Error running dig query: {e}")
        return JsonResponse({
            'success': False,
            'error': f'Error executing dig command: {str(e)}'
        })


def inbox_view(request):
    """Display the Inbox epic directly."""
    ensure_inbox_project()
    epic_id = get_inbox_epic()
    return redirect('epic_detail', project=INBOX_PROJECT_ID, epic=epic_id)


def get_all_projects_for_dropdown():
    """Get all active projects for dropdown selection."""
    projects_dir = safe_join_path('projects')
    projects = []
    if os.path.exists(projects_dir):
        try:
            filenames = [f for f in os.listdir(projects_dir) if f.endswith('.md')]
            for filename in filenames:
                project_id = filename[:-3]
                # Skip inbox - it is the default
                if project_id == INBOX_PROJECT_ID:
                    continue
                metadata, _ = load_project(project_id, metadata_only=True)
                if metadata and not metadata.get('archived', False):
                    projects.append({
                        'id': project_id,
                        'title': metadata.get('title', 'Untitled Project')
                    })
        except OSError:
            pass
    return sorted(projects, key=lambda x: x['title'].lower())


def quick_add(request):
    """Quick add endpoint for creating notes or tasks from the nav bar."""
    if request.method == 'GET':
        # Return projects list for dropdown
        projects = get_all_projects_for_dropdown()
        return JsonResponse({'projects': projects})
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
    
    item_type = request.POST.get('item_type', '').strip()  # 'note' or 'task'
    
    if not item_type:
        logger.warning(f"Quick add: Missing item_type. POST data: {dict(request.POST)}")
        return JsonResponse({'success': False, 'error': 'Missing item_type'}, status=400)
    
    if item_type == 'note':
        title = request.POST.get('title', 'New Note').strip()
        content = request.POST.get('content', '').strip()
        labels = normalize_labels(request.POST.get('labels', ''))
        people = normalize_people(request.POST.get('people', ''))
        
        note_id = f'note-{uuid.uuid4().hex[:8]}'
        metadata = {
            'title': title,
            'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
            'updated': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        if labels:
            metadata['labels'] = labels
        if people:
            metadata['people'] = people
        
        save_note(note_id, metadata, content)
        return JsonResponse({
            'success': True,
            'type': 'note',
            'id': note_id,
            'title': title,
            'url': reverse('note_detail', kwargs={'note_id': note_id})
        })
    
    elif item_type == 'task':
        title = request.POST.get('title', 'New Task').strip()
        if not title:
            return JsonResponse({'success': False, 'error': 'Title is required'}, status=400)
        
        project_id = request.POST.get('project_id', INBOX_PROJECT_ID).strip()
        if not project_id:
            project_id = INBOX_PROJECT_ID
        
        status = request.POST.get('status', 'todo')
        priority = request.POST.get('priority', '').strip() or '3'
        due_date = request.POST.get('due_date', '').strip()
        labels = normalize_labels(request.POST.get('labels', ''))
        content = request.POST.get('content', '').strip()
        
        # Validate project
        if not is_valid_project_id(project_id):
            logger.warning(f"Quick add task: Invalid project_id '{project_id}'. POST data: {dict(request.POST)}")
            return JsonResponse({'success': False, 'error': f'Invalid project: {project_id}'}, status=400)
        
        # Ensure project exists
        project_meta, _ = load_project(project_id, metadata_only=True)
        if not project_meta:
            return JsonResponse({'success': False, 'error': 'Project not found'}, status=404)
        
        # Get or create epic for the project
        if project_id == INBOX_PROJECT_ID:
            epic_id = get_inbox_epic()
        else:
            # For other projects, need to get an epic - use first active epic or create one
            epics_dir = safe_join_path('projects', project_id, 'epics')
            epic_id = None
            if os.path.exists(epics_dir):
                epic_files = [f for f in os.listdir(epics_dir) if f.endswith('.md')]
                for epic_file in epic_files:
                    epic_id_candidate = epic_file[:-3]
                    epic_meta, _ = load_epic(project_id, epic_id_candidate, metadata_only=True)
                    if epic_meta and epic_meta.get('status') == 'active':
                        epic_id = epic_id_candidate
                        break
                # If no active epic found, use the first one
                if not epic_id and epic_files:
                    epic_id = epic_files[0][:-3]
            
            # If still no epic, create a default one
            if not epic_id:
                epic_id = f'epic-{uuid.uuid4().hex[:8]}'
                seq_id = get_next_seq_id(project_id, 'epic')
                epic_metadata = {
                    'title': 'Default',
                    'status': 'active',
                    'seq_id': seq_id,
                    'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
                }
                save_epic(project_id, epic_id, epic_metadata, '')
        
        # Create the task
        task_id = f'task-{uuid.uuid4().hex[:8]}'
        seq_id = get_next_seq_id(project_id, 'task')
        task_metadata = {
            'title': title,
            'status': status,
            'seq_id': seq_id,
            'priority': priority,
            'due_date': due_date,
            'created': datetime.now().strftime('%Y-%m-%d')
        }
        if labels:
            task_metadata['labels'] = labels
        
        add_activity_entry(task_metadata, 'created')
        save_task(project_id, epic_id, task_id, task_metadata, content)
        
        return JsonResponse({
            'success': True,
            'type': 'task',
            'id': task_id,
            'title': title,
            'url': reverse('task_detail', kwargs={'project': project_id, 'epic': epic_id, 'task': task_id})
        })
    
    return JsonResponse({'success': False, 'error': 'Invalid item_type'}, status=400)


def move_task(request, project, epic, task):
    """Move a task from inbox to another project/epic."""
    if request.method == 'GET':
        # Return projects and epics for selection
        projects = get_all_projects_for_dropdown()
        # Include inbox in the list for completeness
        projects.insert(0, {'id': INBOX_PROJECT_ID, 'title': 'Inbox'})
        
        # Get epics for each project
        projects_with_epics = []
        for p in projects:
            p_id = p['id']
            epics = []
            epics_dir = safe_join_path('projects', p_id, 'epics')
            if os.path.exists(epics_dir):
                for epic_file in os.listdir(epics_dir):
                    if epic_file.endswith('.md'):
                        epic_id = epic_file[:-3]
                        epic_meta, _ = load_epic(p_id, epic_id, metadata_only=True)
                        if epic_meta and not epic_meta.get('archived', False):
                            epics.append({
                                'id': epic_id,
                                'title': epic_meta.get('title', 'Untitled Epic'),
                                'seq_id': epic_meta.get('seq_id', '')
                            })
            projects_with_epics.append({
                'id': p_id,
                'title': p['title'],
                'epics': sorted(epics, key=lambda e: (e.get('seq_id', ''), e.get('title', '')))
            })
        
        return JsonResponse({
            'projects': projects_with_epics
        })
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
    
    # Validate current task exists
    task_metadata, task_content = load_task(project, epic, task)
    if not task_metadata:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)
    
    # Get target project and epic
    target_project = request.POST.get('target_project', '').strip()
    target_epic = request.POST.get('target_epic', '').strip()
    
    if not target_project or not target_epic:
        return JsonResponse({'success': False, 'error': 'Target project and epic required'}, status=400)
    
    if not is_valid_project_id(target_project) or not validate_id(target_epic, 'epic'):
        return JsonResponse({'success': False, 'error': 'Invalid target project or epic'}, status=400)
    
    # Verify target epic exists
    target_epic_meta, _ = load_epic(target_project, target_epic, metadata_only=True)
    if not target_epic_meta:
        return JsonResponse({'success': False, 'error': 'Target epic not found'}, status=404)
    
    # Do not allow moving to same location
    if project == target_project and epic == target_epic:
        return JsonResponse({'success': False, 'error': 'Task is already in this location'}, status=400)
    
    try:
        # Generate new seq_id for target project
        new_seq_id = get_next_seq_id(target_project, 'task')
        task_metadata['seq_id'] = new_seq_id
        
        # Update project/epic references in metadata
        task_metadata['project_id'] = target_project
        task_metadata['epic_id'] = target_epic
        
        # Add move activity
        old_location = f"{project}/{epic}"
        new_location = f"{target_project}/{target_epic}"
        add_activity_entry(task_metadata, 'moved', old_location, new_location)
        
        # Load all subtasks first
        old_subtasks_dir = safe_join_path('projects', project, 'epics', epic, 'tasks', task, 'subtasks')
        subtasks_to_move = []
        if os.path.exists(old_subtasks_dir):
            for subtask_file in os.listdir(old_subtasks_dir):
                if subtask_file.endswith('.md'):
                    subtask_id = subtask_file[:-3]
                    subtask_meta, subtask_content = load_subtask(project, epic, task, subtask_id)
                    if subtask_meta:
                        subtasks_to_move.append((subtask_id, subtask_meta, subtask_content))
        
        # Save task to new location
        save_task(target_project, target_epic, task, task_metadata, task_content)
        
        # Move all subtasks
        for subtask_id, subtask_meta, subtask_content in subtasks_to_move:
            # Generate new seq_id for subtask in new project
            new_subtask_seq_id = get_next_seq_id(target_project, 'subtask')
            subtask_meta['seq_id'] = new_subtask_seq_id
            subtask_meta['project_id'] = target_project
            subtask_meta['epic_id'] = target_epic
            subtask_meta['task_id'] = task
            save_subtask(target_project, target_epic, task, subtask_id, subtask_meta, subtask_content)
        
        # Update dependencies: tasks that reference this task need to be updated
        # Find all tasks/subtasks that have this task in their blocks/blocked_by
        all_projects_dir = safe_join_path('projects')
        if os.path.exists(all_projects_dir):
            for p_id in os.listdir(all_projects_dir):
                if not os.path.isdir(os.path.join(all_projects_dir, p_id)):
                    continue
                p_epics_dir = os.path.join(all_projects_dir, p_id, 'epics')
                if not os.path.exists(p_epics_dir):
                    continue
                for e_id in os.listdir(p_epics_dir):
                    e_dir = os.path.join(p_epics_dir, e_id)
                    if not os.path.isdir(e_dir):
                        continue
                    e_tasks_dir = os.path.join(e_dir, 'tasks')
                    if not os.path.exists(e_tasks_dir):
                        continue
                    for t_file in os.listdir(e_tasks_dir):
                        if not t_file.endswith('.md'):
                            continue
                        t_id = t_file[:-3]
                        t_meta, t_content = load_task(p_id, e_id, t_id)
                        if not t_meta:
                            continue
                        
                        updated = False
                        # Check blocks
                        if task in t_meta.get('blocks', []):
                            # Task is referenced, update if needed (dependencies are by ID, so they should still work)
                            # But we should update activity
                            add_activity_entry(t_meta, 'dependency_updated', None, f"blocks {task_metadata.get('title', task)}")
                            updated = True
                        # Check blocked_by
                        if task in t_meta.get('blocked_by', []):
                            add_activity_entry(t_meta, 'dependency_updated', None, f"blocked by {task_metadata.get('title', task)}")
                            updated = True
                        
                        if updated:
                            save_task(p_id, e_id, t_id, t_meta, t_content)
                        
                        # Check subtasks
                        t_subtasks_dir = os.path.join(e_tasks_dir, t_id, 'subtasks')
                        if os.path.exists(t_subtasks_dir):
                            for s_file in os.listdir(t_subtasks_dir):
                                if not s_file.endswith('.md'):
                                    continue
                                s_id = s_file[:-3]
                                s_meta, s_content = load_subtask(p_id, e_id, t_id, s_id)
                                if not s_meta:
                                    continue
                                
                                s_updated = False
                                if task in s_meta.get('blocks', []):
                                    add_activity_entry(s_meta, 'dependency_updated', None, f"blocks {task_metadata.get('title', task)}")
                                    s_updated = True
                                if task in s_meta.get('blocked_by', []):
                                    add_activity_entry(s_meta, 'dependency_updated', None, f"blocked by {task_metadata.get('title', task)}")
                                    s_updated = True
                                
                                if s_updated:
                                    save_subtask(p_id, e_id, t_id, s_id, s_meta, s_content)
        
        # Delete old task file and directory
        old_task_path = safe_join_path('projects', project, 'epics', epic, 'tasks', f'{task}.md')
        old_task_dir = safe_join_path('projects', project, 'epics', epic, 'tasks', task)
        if os.path.exists(old_task_path):
            os.remove(old_task_path)
        if os.path.exists(old_task_dir):
            shutil.rmtree(old_task_dir)
        
        # Update stats for both projects
        update_project_stats(project)
        update_project_stats(target_project)
        
        return JsonResponse({
            'success': True,
            'url': reverse('task_detail', kwargs={'project': target_project, 'epic': target_epic, 'task': task})
        })
    
    except Exception as e:
        logger.error(f"Error moving task: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def upload_image(request):
    """AJAX endpoint to upload images from clipboard paste."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    if 'image' not in request.FILES:
        return JsonResponse({'error': 'No image file provided'}, status=400)
    
    image_file = request.FILES['image']
    
    # Validate file type
    allowed_types = ['image/jpeg', 'image/jpg', 'image/png', 'image/gif', 'image/webp']
    if image_file.content_type not in allowed_types:
        return JsonResponse({'error': 'Invalid file type. Only images are allowed.'}, status=400)
    
    # Validate file size (max 10MB)
    if image_file.size > 10 * 1024 * 1024:
        return JsonResponse({'error': 'File too large. Maximum size is 10MB.'}, status=400)
    
    try:
        # Create uploads directory structure: data/uploads/YYYY/MM/
        now = datetime.now()
        uploads_dir = safe_join_path('uploads', now.strftime('%Y'), now.strftime('%m'))
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Generate unique filename
        file_ext = os.path.splitext(image_file.name)[1] or '.png'
        if not file_ext.startswith('.'):
            file_ext = '.' + file_ext
        filename = f'{uuid.uuid4().hex[:8]}{file_ext}'
        file_path = os.path.join(uploads_dir, filename)
        
        # Save file
        with open(file_path, 'wb') as f:
            for chunk in image_file.chunks():
                f.write(chunk)
        
        # Return relative URL path for markdown
        # Path will be: /static/uploads/YYYY/MM/filename.ext
        relative_path = f'uploads/{now.strftime("%Y")}/{now.strftime("%m")}/{filename}'
        
        return JsonResponse({
            'success': True,
            'url': f'/static/{relative_path}',
            'path': relative_path
        })
        
    except Exception as e:
        logger.error(f"Error uploading image: {e}")
        return JsonResponse({
            'success': False,
            'error': f'Error saving image: {str(e)}'
        }, status=500)


def mac_lookup(request):
    """AJAX endpoint to lookup MAC address vendor using macvendors.com API."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    
    mac = request.POST.get('mac', '').strip()
    if not mac:
        return JsonResponse({'error': 'MAC address is required'}, status=400)
    
    # Normalize MAC address - remove colons, dashes, spaces, and convert to uppercase
    mac_normalized = re.sub(r'[:-]', '', mac).upper()
    
    # Validate MAC address format (should be 12 hex characters)
    if not re.match(r'^[0-9A-F]{12}$', mac_normalized):
        return JsonResponse({'error': 'Invalid MAC address format. Use format like FC-A1-3E-2A-1C-33, FC:A1:3E:2A:1C:33, or fca13e2a1c33'}, status=400)
    
    # Format MAC address for API (FC-A1-3E-2A-1C-33)
    mac_formatted = '-'.join([mac_normalized[i:i+2] for i in range(0, 12, 2)])
    
    try:
        # Call macvendors.com API
        url = f'https://api.macvendors.com/{mac_formatted}'
        
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'LazyNetworkEngineer/1.0')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            vendor = response.read().decode('utf-8').strip()
            
            if not vendor:
                return JsonResponse({
                    'success': False,
                    'error': 'No vendor information found for this MAC address'
                })
            
            return JsonResponse({
                'success': True,
                'mac': mac_formatted,
                'vendor': vendor
            })
            
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return JsonResponse({
                'success': False,
                'error': 'MAC address not found in database'
            })
        else:
            logger.error(f"HTTP error in MAC lookup: {e}")
            return JsonResponse({
                'success': False,
                'error': f'API error: HTTP {e.code}'
            })
    except urllib.error.URLError as e:
        logger.error(f"URL error in MAC lookup: {e}")
        return JsonResponse({
            'success': False,
            'error': 'Network error: Unable to reach MAC vendors API'
        })
    except Exception as e:
        logger.error(f"Error in MAC lookup: {e}")
        return JsonResponse({
            'success': False,
            'error': f'Error looking up MAC address: {str(e)}'
        })
