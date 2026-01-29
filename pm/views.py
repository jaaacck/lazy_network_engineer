import os
import logging
import hashlib
import re
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, date, timedelta
import uuid
import markdown
import bleach
import time
import json
from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import JsonResponse, Http404
from django.core.cache import cache
from django.contrib import messages
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.db import transaction
from .utils import (
    validate_id, safe_join_path, 
    calculate_markdown_progress, calculate_checklist_progress
)
from .models import (
    Status, Person, Label, Update,
    Project, Epic, Task, Subtask, Note, EntityPersonLink, EntityLabelLink
)
from django.contrib.contenttypes.models import ContentType
from .storage.index_storage import IndexStorage

# Initialize index storage
index_storage = IndexStorage()

logger = logging.getLogger('pm')
STATS_VERSION = 1

# Entity type to model mapping
ENTITY_TYPE_MAP = {
    'project': Project,
    'epic': Epic,
    'task': Task,
    'subtask': Subtask,
    'note': Note,
}

def get_entity_model(entity_type):
    """Get the model class for a given entity type."""
    return ENTITY_TYPE_MAP.get(entity_type)

def get_entity_by_id(entity_id):
    """Get an entity by ID, trying all model types."""
    for model_class in ENTITY_TYPE_MAP.values():
        try:
            return model_class.objects.get(id=entity_id)
        except model_class.DoesNotExist:
            continue
    raise Http404(f"Entity with id {entity_id} not found")

# Allowed HTML tags and attributes for bleach sanitization (matching markdown_extras.py)
ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'a', 'code', 'pre', 'blockquote', 'hr',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span', 'input', 'img'
]

ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title'],
    'table': ['class'],
    'pre': ['class'],
    'code': ['class'],
    'blockquote': ['class'],
    'div': ['class'],
    'span': ['class'],
    'input': ['type', 'checked', 'disabled'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'style']
}

def render_markdown(value):
    """Convert markdown to HTML and sanitize output (matching markdownify filter)."""
    if not value:
        return ""
    
    cache_key = f"md:{hashlib.md5(value.encode('utf-8')).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    # Convert markdown to HTML
    html = markdown.markdown(
        value,
        extensions=[
            'markdown.extensions.extra',
            'markdown.extensions.sane_lists',
            'markdown.extensions.nl2br',
            'markdown.extensions.fenced_code'
        ]
    )
    
    # Handle tickboxes [ ] and [x]
    html = re.sub(r'\[ \]', r'<input type="checkbox" disabled>', html)
    html = re.sub(r'\[[xX]\]', r'<input type="checkbox" checked disabled>', html)
    
    # Sanitize HTML to prevent XSS
    html = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True
    )
    
    cache.set(cache_key, html, 3600)
    return html

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


def enrich_updates_with_stored_types(entity_id, updates_list):
    """Enrich updates from metadata with type/activity_type from Update table.
    
    For each update, look up the stored type/activity_type from the Update table
    to replace defaults/missing values in metadata.
    """
    if not updates_list or not entity_id:
        return updates_list
    
    # Build map of timestamp -> (type, activity_type) from Update table
    update_map = {}
    try:
        stored_updates = Update.objects.filter(entity_id=entity_id).values('timestamp', 'type', 'activity_type')
        for u in stored_updates:
            update_map[u['timestamp']] = {
                'type': u['type'],
                'activity_type': u['activity_type']
            }
    except Exception as e:
        logger.warning(f"Could not load stored update types for {entity_id}: {e}")
    
    # Enrich metadata updates with stored values
    enriched = []
    for update in updates_list:
        update_copy = update.copy() if isinstance(update, dict) else update
        timestamp = update_copy.get('timestamp')
        
        if timestamp and timestamp in update_map:
            # Use stored values from Update table
            stored = update_map[timestamp]
            update_copy['type'] = stored['type']
            update_copy['activity_type'] = stored['activity_type']
        elif 'type' not in update_copy:
            # Default to 'user' for backward compatibility
            update_copy['type'] = 'user'
        
        enriched.append(update_copy)
    
    return enriched


def add_activity_entry(metadata, activity_type, old_value=None, new_value=None, details=None):
    """Add a system activity entry to metadata and Update table.
    
    Args:
        metadata: The entity metadata dict (must have 'id' field)
        activity_type: Type of activity (e.g., 'status_changed', 'priority_changed', 'label_added')
        old_value: Previous value (optional)
        new_value: New value (optional)
        details: Additional details dict (optional)
    """
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
    
    timestamp = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    content = ' '.join(message_parts)
    
    # Add to metadata dict for backward compatibility
    if 'updates' not in metadata:
        metadata['updates'] = []
    
    activity_entry = {
        'timestamp': timestamp,
        'content': content,
        'type': 'system',
        'activity_type': activity_type
    }
    
    if details:
        activity_entry['details'] = details
    
    metadata['updates'].append(activity_entry)
    
    # Also save to Update table
    entity_id = metadata.get('id')
    if entity_id:
        Update.objects.create(
            entity_id=entity_id,
            content=content,
            timestamp=timestamp,
            type='system',
            activity_type=activity_type
        )


def get_all_labels_in_system():
    """Get all unique labels used across epics, tasks, subtasks, and notes."""
    cache_key = "all_labels:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    # Query all labels that are linked to any entity via EntityLabelLink
    labels = set()
    for label_link in EntityLabelLink.objects.select_related('label').all():
        labels.add(label_link.label.name)
    
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
    
    # Try to find person by name (case-insensitive)
    try:
        person = Person.objects.filter(name__iexact=person_normalized).first()
        if person:
            cache.set(cache_key, person.id, 300)
            return person.id
    except Exception as e:
        logger.warning(f"Error finding person by name '{person_normalized}': {e}")
    
    return None


def load_person(person_id, metadata_only=False):
    """Load a person from database by person_id."""
    if not validate_id(person_id, 'person'):
        return None, None
    
    try:
        person = Person.objects.get(id=person_id)
        # Build metadata from Person model fields
        metadata = {
            'id': person.id,
            'name': person.name,
            'display_name': person.display_name or '',
            'email': person.email or '',
            'phone': person.phone or '',
            'job_title': person.job_title or '',
            'company': person.company or '',
            'created': person.created.isoformat() if person.created else '',
            'updated': person.updated.isoformat() if person.updated else '',
            'notes': person.notes or [],
        }
        
        # Merge with metadata_json if it exists (for backward compatibility with old data)
        if person.metadata_json:
            try:
                json_metadata = json.loads(person.metadata_json)
                # Only merge fields that aren't already set from model fields
                for key, value in json_metadata.items():
                    if key not in metadata or not metadata[key]:
                        metadata[key] = value
            except (json.JSONDecodeError, TypeError):
                pass
        
        # Get content from Person model (ad-hoc notes)
        content = person.content if not metadata_only else None
        
        return metadata, content
    except Person.DoesNotExist:
        return None, None


def save_person(person_id, metadata, content=''):
    """Save a person to database by person_id."""
    if not validate_id(person_id, 'person'):
        raise Http404("Invalid person ID")
    
    # Ensure person_id is in metadata
    metadata['id'] = person_id
    
    # Ensure person has a default "Active" status for database compatibility
    if 'status' not in metadata:
        metadata['status'] = 'active'

    # Update or create Person record with proper field mapping
    person, created = Person.objects.update_or_create(
        id=person_id,
        defaults={
            'name': metadata.get('name', '').strip().lstrip('@'),
            'display_name': metadata.get('display_name', ''),
            'email': metadata.get('email', ''),
            'phone': metadata.get('phone', ''),
            'job_title': metadata.get('job_title', ''),
            'company': metadata.get('company', ''),
            'notes': metadata.get('notes', []),
            'content': content or '',
            'metadata_json': json.dumps(metadata),  # Keep for backward compatibility
        }
    )
    
    # Note: Person is not an entity type, so we don't sync to entity index
    # Person records are standalone and referenced via EntityPersonLink


def ensure_person_exists(person_name):
    """Ensure a person exists by name. Creates the person if it doesn't exist.
    Returns the normalized person name.
    This function should be called whenever a person is added to any entity."""
    if not person_name:
        return person_name
    
    person_normalized = person_name.strip().lstrip('@')
    if not person_normalized:
        return person_name
    
    # Check if person already exists
    person_id = find_person_by_name(person_normalized)
    
    # If person doesn't exist, create it
    if not person_id:
        person_id = f'person-{uuid.uuid4().hex[:8]}'
        metadata = {
            'name': person_normalized,
            'created': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        save_person(person_id, metadata)
        # Invalidate caches
        cache.delete("all_people:v1")
        cache.delete("all_people:v3")
        cache.delete("person_name_to_id_map:v1")
        logger.info(f"Auto-created person '{person_normalized}' with ID {person_id}")
    
    return person_normalized


def ensure_people_exist(people_list):
    """Ensure all people in a list exist. Creates any missing persons.
    Returns a list of normalized person names.
    This function should be called whenever a list of people is set on any entity."""
    if not people_list:
        return people_list
    
    normalized_people = []
    for person_name in people_list:
        normalized = ensure_person_exists(person_name)
        if normalized:
            normalized_people.append(normalized)
    
    return normalized_people


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
    
    # First, get all standalone people from database
    people = Person.objects.all()
    for person in people:
        people_ids.add(person.id)
    
    # Then scan EntityPersonLink for all people references
    for entity_person_link in EntityPersonLink.objects.select_related('person').all():
        people_ids.add(entity_person_link.person.id)
    
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
    note_entities = Note.objects.all()
    for entity in note_entities:
        notes.append({
            'id': entity.id,
            'title': entity.title or 'Untitled Note',
            'created': entity.created or ''
        })
    
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
    
    # Load projects
    projects = Project.objects.all()
    for entity in projects:
        entities['projects'].append({
            'id': entity.id,
            'title': entity.title or 'Untitled Project',
            'seq_id': ''
        })
    
    # Load epics
    epics = Epic.objects.all()
    for entity in epics:
        entities['epics'].append({
            'id': entity.id,
            'project_id': entity.project_id,
            'title': entity.title or 'Untitled Epic',
            'seq_id': entity.seq_id or ''
        })
    
    # Load tasks
    tasks = Task.objects.all()
    for entity in tasks:
        entities['tasks'].append({
            'id': entity.id,
            'project_id': entity.project_id,
            'epic_id': entity.epic_id,
            'title': entity.title or 'Untitled Task',
            'seq_id': entity.seq_id or ''
        })
    
    # Load subtasks
    subtasks = Subtask.objects.all()
    for entity in subtasks:
        entities['subtasks'].append({
            'id': entity.id,
            'project_id': entity.project_id,
            'epic_id': entity.epic_id,
            'task_id': entity.task_id,
            'title': entity.title or 'Untitled Subtask',
            'seq_id': entity.seq_id or ''
        })
    
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
    
    # Query each entity type separately
    for entity in Project.objects.all():
        notes_list = entity.notes or []
        if note_id in notes_list:
            backlinks['projects'].append({
                'id': entity.id,
                'title': entity.title or 'Untitled Project'
            })
    
    for entity in Epic.objects.all():
        notes_list = entity.notes or []
        if note_id in notes_list:
            backlinks['epics'].append({
                'id': entity.id,
                'project_id': entity.project_id,
                'title': entity.title or 'Untitled Epic',
                'seq_id': entity.seq_id or ''
            })
    
    for entity in Task.objects.all():
        notes_list = entity.notes or []
        if note_id in notes_list:
            backlinks['tasks'].append({
                'id': entity.id,
                'project_id': entity.project_id,
                'epic_id': entity.epic_id,
                'title': entity.title or 'Untitled Task',
                'seq_id': entity.seq_id or ''
            })
    
    for entity in Subtask.objects.all():
        notes_list = entity.notes or []
        if note_id in notes_list:
            backlinks['subtasks'].append({
                'id': entity.id,
                'project_id': entity.project_id,
                'epic_id': entity.epic_id,
                'task_id': entity.task_id,
                'title': entity.title or 'Untitled Subtask',
                'seq_id': entity.seq_id or ''
            })
    
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
    
    # Query all entities of this type in the project
    model_class = get_entity_model(entity_type)
    if not model_class:
        return f"{prefix}1"
    
    entities = model_class.objects.filter(project_id=project_id)
    
    for entity in entities:
        seq = entity.seq_id or ''
        if seq and seq.startswith(prefix):
            try:
                # Extract number after prefix
                num_str = seq[len(prefix):]
                num = int(num_str)
                max_seq = max(max_seq, num)
            except ValueError:
                pass
    
    return f'{prefix}{max_seq + 1}'


def _merge_people_from_entityperson(entity, metadata):
    """Merge people from EntityPersonLink relationships into metadata if not already present."""
    if 'people' not in metadata or not metadata['people']:
        # Get the ContentType for this entity
        content_type = ContentType.objects.get_for_model(entity)
        # Load people from EntityPersonLink relationships
        entity_people = EntityPersonLink.objects.filter(
            content_type=content_type,
            object_id=entity.id
        ).select_related('person')
        people_names = [ep.person.name for ep in entity_people]
        if people_names:
            metadata['people'] = people_names
    return metadata




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


def _build_metadata_from_entity(entity):
    """Build metadata dict from Entity database fields."""
    
    # Get ContentType for generic relationships
    content_type = ContentType.objects.get_for_model(entity)
    
    # Get labels and people using generic relationships
    labels = [el.label.name for el in EntityLabelLink.objects.filter(
        content_type=content_type, object_id=entity.id
    ).select_related('label')]
    
    people = [ep.person.name for ep in EntityPersonLink.objects.filter(
        content_type=content_type, object_id=entity.id
    ).select_related('person')]
    
    # Build metadata from Entity fields
    metadata = {
        'id': entity.id,
        'title': entity.title,
        'status': entity.status_fk.name if entity.status_fk else '',
        'priority': entity.priority,
        'created': entity.created or '',
        'updated': entity.updated or '',
        'due_date': entity.due_date_dt.isoformat() if entity.due_date_dt else '',
        'schedule_start': entity.schedule_start_dt.isoformat() if entity.schedule_start_dt else '',
        'schedule_end': entity.schedule_end_dt.isoformat() if entity.schedule_end_dt else '',
        'labels': labels,
        'people': people,
        'notes': getattr(entity, 'notes', []) or [],
        'dependencies': getattr(entity, 'dependencies', []) or [],
        'checklist': getattr(entity, 'checklist', []) or [],
        'stats': getattr(entity, 'stats', {}) or {},
        'seq_id': entity.seq_id or '',
        'archived': entity.archived,
        'is_inbox_epic': getattr(entity, 'is_inbox_epic', False),
        'color': getattr(entity, 'color', '') or '',
        'stats_version': getattr(entity, 'stats_version', None),
        'stats_updated': entity.stats_updated.isoformat() if hasattr(entity, 'stats_updated') and entity.stats_updated else '',
        'updates': [{'timestamp': u.timestamp, 'content': u.content} 
                   for u in Update.objects.filter(entity_id=entity.id).order_by('timestamp')],
    }
    
    # Add relationship fields based on entity type
    if hasattr(entity, 'project') and entity.project:
        metadata['project_id'] = entity.project_id
    if hasattr(entity, 'epic') and entity.epic:
        metadata['epic_id'] = entity.epic_id
    if hasattr(entity, 'task') and entity.task:
        metadata['task_id'] = entity.task_id
    
    return metadata



def load_project(project_id, metadata_only=False):
    """Load a project from database."""
    if not is_valid_project_id(project_id):
        logger.warning(f"Invalid project ID: {project_id}")
        return None, None

    try:
        entity = Project.objects.select_related('status_fk').get(id=project_id)
        # Build metadata from Entity fields
        metadata = _build_metadata_from_entity(entity)
        metadata = _merge_people_from_entityperson(entity, metadata)
        metadata['status_display'] = get_status_display(entity)
        content = entity.content if not metadata_only else None
        return metadata, content
    except Project.DoesNotExist:
        return None, None


def save_project(project_id, metadata, content):
    """Save a project to database."""
    if not is_valid_project_id(project_id):
        raise Http404("Invalid project ID")

    # Extract updates text, people tags, labels for search
    updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
    people_tags = metadata.get('people', [])
    labels = metadata.get('labels', [])
    
    # Save to database and sync search index
    index_storage.sync_entity(
        entity_id=project_id,
        entity_type='project',
        metadata=metadata,
        content=content or '',
        updates_text=updates_text,
        people_tags=people_tags,
        labels=labels
    )


def load_epic(project_id, epic_id, metadata_only=False):
    """Load an epic from database."""
    if not is_valid_project_id(project_id) or not validate_id(epic_id, 'epic'):
        logger.warning(f"Invalid IDs: project={project_id}, epic={epic_id}")
        return None, None

    try:
        entity = Epic.objects.select_related('status_fk', 'project').get(id=epic_id, project_id=project_id)
        # Build metadata from Entity fields
        metadata = _build_metadata_from_entity(entity)
        if 'project_id' not in metadata:
            metadata['project_id'] = project_id
        metadata = _merge_people_from_entityperson(entity, metadata)
        metadata['status_display'] = get_status_display(entity)
        content = entity.content if not metadata_only else None
        return metadata, content
    except Epic.DoesNotExist:
        return None, None


def save_epic(project_id, epic_id, metadata, content):
    """Save an epic to database."""
    if not is_valid_project_id(project_id) or not validate_id(epic_id, 'epic'):
        raise Http404("Invalid IDs")

    # Ensure project_id is in metadata for relationship tracking
    if 'project_id' not in metadata:
        metadata['project_id'] = project_id
    
    # Extract updates text, people tags, labels for search
    updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
    people_tags = metadata.get('people', [])
    labels = metadata.get('labels', [])
    
    # Save to database and sync search index
    index_storage.sync_entity(
        entity_id=epic_id,
        entity_type='epic',
        metadata=metadata,
        content=content or '',
        updates_text=updates_text,
        people_tags=people_tags,
        labels=labels
    )
    update_project_stats(project_id)


def load_task(project_id, task_id, epic_id=None, metadata_only=False):
    """Load a task from database. Epic is optional - if None, task is directly under project."""
    if not (is_valid_project_id(project_id) and validate_id(task_id, 'task')):
        logger.warning(f"Invalid IDs: project={project_id}, task={task_id}")
        return None, None
    
    # Validate epic_id if provided
    if epic_id is not None and not validate_id(epic_id, 'epic'):
        logger.warning(f"Invalid epic ID: {epic_id}")
        return None, None

    try:
        query = Task.objects.select_related('status_fk', 'project', 'epic').filter(id=task_id, project_id=project_id)
        if epic_id:
            query = query.filter(epic_id=epic_id)
        else:
            # Match NULL epic_id (tasks without an epic)
            query = query.filter(epic__isnull=True)
        
        entity = query.get()
        # Build metadata from Entity fields
        metadata = _build_metadata_from_entity(entity)
        if 'project_id' not in metadata:
            metadata['project_id'] = project_id
        if epic_id and 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
        elif not epic_id and 'epic_id' in metadata:
            metadata.pop('epic_id', None)
        metadata = _merge_people_from_entityperson(entity, metadata)
        metadata['status_display'] = get_status_display(entity)
        content = entity.content if not metadata_only else None
        return metadata, content
    except Task.DoesNotExist:
        return None, None


def save_task(project_id, task_id, metadata, content, epic_id=None):
    """Save a task to database. Epic is optional - if None, task is directly under project."""
    if not (is_valid_project_id(project_id) and validate_id(task_id, 'task')):
        raise Http404("Invalid IDs")
    
    # Validate epic_id if provided
    if epic_id is not None and not validate_id(epic_id, 'epic'):
        raise Http404("Invalid epic ID")

    # Determine epic_id from metadata if not provided
    if epic_id is None:
        epic_id = metadata.get('epic_id')
    
    # Ensure relationship IDs are in metadata
    if 'project_id' not in metadata:
        metadata['project_id'] = project_id
    if epic_id:
        if 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
    else:
        # Remove epic_id if task is not under an epic
        metadata.pop('epic_id', None)
    
    # Extract updates text, people tags, labels for search
    updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
    people_tags = metadata.get('people', [])
    labels = metadata.get('labels', [])
    
    # Save to database and sync search index
    index_storage.sync_entity(
        entity_id=task_id,
        entity_type='task',
        metadata=metadata,
        content=content or '',
        updates_text=updates_text,
        people_tags=people_tags,
        labels=labels
    )
    update_project_stats(project_id)


def load_subtask(project_id, task_id, subtask_id, epic_id=None, metadata_only=False):
    """Load a subtask from database. Epic is optional - if None, task is directly under project."""
    if not (is_valid_project_id(project_id) and
            validate_id(task_id, 'task') and
            validate_id(subtask_id, 'subtask')):
        logger.warning(f"Invalid IDs: project={project_id}, task={task_id}, subtask={subtask_id}")
        return None, None
    
    # Validate epic_id if provided
    if epic_id is not None and not validate_id(epic_id, 'epic'):
        logger.warning(f"Invalid epic ID: {epic_id}")
        return None, None

    try:
        query = Subtask.objects.select_related('status_fk', 'project', 'task', 'epic').filter(id=subtask_id, project_id=project_id, task_id=task_id)
        if epic_id:
            query = query.filter(epic_id=epic_id)
        else:
            # Match NULL epic_id (subtasks without an epic)
            query = query.filter(epic__isnull=True)
        
        entity = query.get()
        # Build metadata from Entity fields
        metadata = _build_metadata_from_entity(entity)
        if 'project_id' not in metadata:
            metadata['project_id'] = project_id
        if epic_id and 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
        elif not epic_id and 'epic_id' in metadata:
            metadata.pop('epic_id', None)
        if 'task_id' not in metadata:
            metadata['task_id'] = task_id
        metadata = _merge_people_from_entityperson(entity, metadata)
        metadata['status_display'] = get_status_display(entity)
        content = entity.content if not metadata_only else None
        return metadata, content
    except Subtask.DoesNotExist:
        return None, None


def save_subtask(project_id, task_id, subtask_id, metadata, content, epic_id=None):
    """Save a subtask to database. Epic is optional - if None, task is directly under project."""
    if not (is_valid_project_id(project_id) and
            validate_id(task_id, 'task') and
            validate_id(subtask_id, 'subtask')):
        raise Http404("Invalid IDs")
    
    # Validate epic_id if provided
    if epic_id is not None and not validate_id(epic_id, 'epic'):
        raise Http404("Invalid epic ID")

    # Determine epic_id from metadata if not provided
    if epic_id is None:
        epic_id = metadata.get('epic_id')
    
    # Ensure relationship IDs are in metadata
    if 'project_id' not in metadata:
        metadata['project_id'] = project_id
    if epic_id:
        if 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
    else:
        metadata.pop('epic_id', None)
    if 'task_id' not in metadata:
        metadata['task_id'] = task_id
    
    # Extract updates text, people tags, labels for search
    updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
    people_tags = metadata.get('people', [])
    labels = metadata.get('labels', [])
    
    # Save to database and sync search index
    index_storage.sync_entity(
        entity_id=subtask_id,
        entity_type='subtask',
        metadata=metadata,
        content=content or '',
        updates_text=updates_text,
        people_tags=people_tags,
        labels=labels
    )
    update_project_stats(project_id)


def compute_project_stats(project_id):
    """Compute project overview stats for list view."""
    # Count epics
    epics_count = Epic.objects.filter(project_id=project_id).count()
    
    # Count tasks (with and without epic)
    tasks = Task.objects.filter(project_id=project_id)
    tasks_count = tasks.count()
    done_tasks_count = tasks.filter(status_fk__name='done').count()
    
    # Count subtasks
    subtasks = Subtask.objects.filter(project_id=project_id)
    subtasks_count = subtasks.count()
    done_subtasks_count = subtasks.filter(status_fk__name='done').count()

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
    epics = Epic.objects.filter(project_id=INBOX_PROJECT_ID)
    if not epics.exists():
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
    
    # Query for inbox epic
    epics = Epic.objects.filter(project_id=INBOX_PROJECT_ID)
    for epic in epics:
        if epic.is_inbox_epic:
            return epic.id
    
    # If no inbox epic found, use the first one
    first_epic = epics.first()
    if first_epic:
        return first_epic.id
    
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
    show_archived = request.GET.get('archived', 'false') == 'true'

    projects = []
    project_entities = Project.objects.select_related('status_fk').exclude(id=INBOX_PROJECT_ID)
    
    for entity in project_entities:
        is_archived = entity.archived
        if (show_archived and not is_archived) or (not show_archived and is_archived):
            continue
        
        stats = entity.stats or {}
        if entity.stats_version != STATS_VERSION or not stats:
            stats = compute_project_stats(entity.id)
            metadata = _build_metadata_from_entity(entity)
            metadata['stats'] = stats
            metadata['stats_version'] = STATS_VERSION
            metadata['stats_updated'] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
            save_project(entity.id, metadata, entity.content)

        status_name = entity.status_fk.name if entity.status_fk else 'active'
        projects.append({
            'id': entity.id,
            'title': entity.title or 'Untitled Project',
            'status': status_name,
            'status_display': get_status_display(entity),
            'archived': is_archived,
            'epics_count': stats.get('epics_count', 0),
            'tasks_count': stats.get('tasks_count', 0),
            'done_tasks_count': stats.get('done_tasks_count', 0),
            'subtasks_count': stats.get('subtasks_count', 0),
            'done_subtasks_count': stats.get('done_subtasks_count', 0),
            'completion_percentage': stats.get('completion_percentage', 0)
        })

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

    # Handle quick updates (status, etc.)
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        if quick_update == 'status':
            old_status = metadata.get('status', 'active')
            new_status = request.POST.get('status', old_status)
            if old_status != new_status:
                metadata['status'] = new_status
                add_activity_entry(metadata, 'status_changed', old_status, new_status)
                save_project(project, metadata, content)
            return redirect('project_detail', project=project)
        elif quick_update == 'description':
            new_content = request.POST.get('description', '').strip()
            content = new_content
            # Extract @mentions from description content
            mentions = extract_mentions(new_content)
            if mentions:
                # Ensure persons exist and merge into metadata['people']
                normalized_mentions = ensure_people_exist(mentions)
                current_people = normalize_people(metadata.get('people', []))
                # Merge mentions, avoiding duplicates
                for mention in normalized_mentions:
                    if mention.lower() not in [p.lower() for p in current_people]:
                        current_people.append(mention)
                metadata['people'] = current_people
            save_project(project, metadata, content)
            # Return JSON for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                rendered = render_markdown(new_content)
                return JsonResponse({
                    'success': True,
                    'content': rendered
                })
            return redirect('project_detail', project=project)

    # Calculate project-level progress
    _, markdown_total, markdown_progress = calculate_markdown_progress(content)
    _, checklist_total, checklist_progress = calculate_checklist_progress(metadata)

    # Load epics and their tasks
    epics = []
    archived_epics = []
    open_epics = []

    epic_entities = Epic.objects.select_related('status_fk', 'project').filter(project_id=project)
    for epic_entity in epic_entities:
        epic_metadata = _build_metadata_from_entity(epic_entity)
        
        is_archived = epic_entity.archived if hasattr(epic_entity, 'archived') else epic_metadata.get('archived', False)

        # Load tasks for this epic
        tasks = []
        open_tasks = []
        
        task_entities = Task.objects.select_related('status_fk', 'project', 'epic').filter(project_id=project, epic_id=epic_entity.id)
        for task_entity in task_entities:
            task_metadata = _build_metadata_from_entity(task_entity)
            
            status_name = task_entity.status_fk.name if task_entity.status_fk else task_metadata.get('status', 'todo')
            task_data = {
                'id': task_entity.id,
                'title': task_entity.title or task_metadata.get('title', 'Untitled Task'),
                'status': status_name,
                'status_display': get_status_display(task_entity),
                'schedule_start': task_entity.schedule_start_dt.isoformat() if task_entity.schedule_start_dt else task_metadata.get('schedule_start', ''),
                'schedule_end': task_entity.schedule_end_dt.isoformat() if task_entity.schedule_end_dt else task_metadata.get('schedule_end', '')
            }
            tasks.append(task_data)
            
            # Check if it is an open task
            if task_data['status'] in ['todo', 'in_progress']:
                # Load subtasks for open tasks
                open_subtasks = []
                subtask_entities = Subtask.objects.select_related('status_fk', 'project', 'task', 'epic').filter(project_id=project, task_id=task_entity.id, epic_id=epic_entity.id)
                for subtask_entity in subtask_entities:
                    subtask_metadata = _build_metadata_from_entity(subtask_entity)
                    
                    subtask_status = subtask_entity.status_fk.name if subtask_entity.status_fk else subtask_metadata.get('status', 'todo')
                    if subtask_status in ['todo', 'in_progress']:
                        open_subtasks.append({
                            'id': subtask_entity.id,
                            'title': subtask_entity.title or subtask_metadata.get('title', 'Untitled Subtask'),
                            'status': subtask_status,
                            'status_display': get_status_display(subtask_entity)
                        })
                
                open_task_data = task_data.copy()
                open_task_data['subtasks'] = open_subtasks
                open_tasks.append(open_task_data)

        # Calculate progress
        total_tasks_count = len(tasks)
        completed_tasks_count = sum(1 for t in tasks if t['status'] == 'done')
        progress_pct = (completed_tasks_count / total_tasks_count * 100) if total_tasks_count > 0 else 0

        epic_status = epic_entity.status_fk.name if epic_entity.status_fk else epic_metadata.get('status', 'active')
        epic_data = {
            'id': epic_entity.id,
            'title': epic_entity.title or epic_metadata.get('title', 'Untitled Epic'),
            'status': epic_status,
            'status_display': get_status_display(epic_entity),
            'seq_id': epic_entity.seq_id or epic_metadata.get('seq_id', ''),
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

    # Sort epics by seq_id, then by title
    epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
    archived_epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
    open_epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))

    # Load tasks directly under project (without epic)
    direct_tasks = []
    direct_open_tasks = []
    
    direct_task_entities = Task.objects.select_related('status_fk', 'project').filter(project_id=project, epic__isnull=True)
    for task_entity in direct_task_entities:
        task_metadata = _build_metadata_from_entity(task_entity)
        
        task_status = task_entity.status_fk.name if task_entity.status_fk else task_metadata.get('status', 'todo')
        task_data = {
            'id': task_entity.id,
            'title': task_entity.title or task_metadata.get('title', 'Untitled Task'),
            'status': task_status,
            'status_display': get_status_display(task_entity),
            'seq_id': task_entity.seq_id or task_metadata.get('seq_id', ''),
            'priority': task_entity.priority or task_metadata.get('priority', ''),
            'created': task_entity.created or task_metadata.get('created', ''),
            'due_date': task_entity.due_date_dt.isoformat() if task_entity.due_date_dt else task_metadata.get('due_date', ''),
            'schedule_start': task_entity.schedule_start_dt.isoformat() if task_entity.schedule_start_dt else task_metadata.get('schedule_start', ''),
            'schedule_end': task_entity.schedule_end_dt.isoformat() if task_entity.schedule_end_dt else task_metadata.get('schedule_end', ''),
            'epic_id': None  # Mark as direct task
        }
        direct_tasks.append(task_data)
        
        # Check if it is an open task
        if task_data['status'] in ['todo', 'in_progress']:
            open_subtasks = []
            subtask_entities = Subtask.objects.select_related('status_fk', 'project', 'task').filter(project_id=project, task_id=task_entity.id, epic__isnull=True)
            for subtask_entity in subtask_entities:
                subtask_metadata = _build_metadata_from_entity(subtask_entity)
                
                subtask_status = subtask_entity.status_fk.name if subtask_entity.status_fk else subtask_metadata.get('status', 'todo')
                if subtask_status in ['todo', 'in_progress']:
                    open_subtasks.append({
                        'id': subtask_entity.id,
                        'title': subtask_entity.title or subtask_metadata.get('title', 'Untitled Subtask'),
                        'status': subtask_status,
                        'status_display': get_status_display(subtask_entity)
                    })
            
            open_task_data = task_data.copy()
            open_task_data['subtasks'] = open_subtasks
            direct_open_tasks.append(open_task_data)

    # Handle archive/unarchive
    if request.method == 'POST' and 'archive' in request.POST:
        metadata['archived'] = True
        save_project(project, metadata, content)
        return redirect('project_list')
    
    if request.method == 'POST' and 'unarchive' in request.POST:
        metadata['archived'] = False
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
        'direct_tasks': direct_tasks,
        'direct_open_tasks': direct_open_tasks,
        'activity': activity,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total,
        'all_statuses': get_status_for_entity_type('project')
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
    task_entities = Task.objects.select_related('status_fk').filter(project_id=project, epic_id=epic)
    tasks = []
    for entity in task_entities:
        task_metadata = _build_metadata_from_entity(entity)
        task_status = entity.status_fk.name if entity.status_fk else task_metadata.get('status', 'todo')
        tasks.append({
            'id': entity.id,
            'title': entity.title or task_metadata.get('title', 'Untitled Task'),
            'status': task_status,
            'status_display': get_status_display(entity),
            'seq_id': entity.seq_id or task_metadata.get('seq_id', ''),
            'priority': entity.priority or task_metadata.get('priority', ''),
            'created': entity.created or task_metadata.get('created', ''),
            'due_date': entity.due_date_dt.isoformat() if entity.due_date_dt else task_metadata.get('due_date', ''),
            'order': task_metadata.get('order', 0)
        })

    tasks.sort(key=lambda t: (t.get('order', 0), t.get('title', '')))

    # Calculate progress
    total_tasks = len(tasks)
    completed_tasks = sum(1 for task in tasks if task['status'] == 'done')
    progress_percentage = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0

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
                # Ensure person exists (create if needed)
                person_normalized = ensure_person_exists(person)
                people_list = normalize_people(metadata.get('people', []))
                if person_normalized not in people_list:
                    people_list.append(person_normalized)
                    metadata['people'] = people_list
                    add_activity_entry(metadata, 'person_added', None, person_normalized)
                    save_epic(project, epic, metadata, content)
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
        elif quick_update == 'description':
            new_content = request.POST.get('description', '').strip()
            content = new_content
            # Extract @mentions from description content
            mentions = extract_mentions(new_content)
            if mentions:
                # Ensure persons exist and merge into metadata['people']
                normalized_mentions = ensure_people_exist(mentions)
                current_people = normalize_people(metadata.get('people', []))
                # Merge mentions, avoiding duplicates
                for mention in normalized_mentions:
                    if mention.lower() not in [p.lower() for p in current_people]:
                        current_people.append(mention)
                metadata['people'] = current_people
            save_epic(project, epic, metadata, content)
            # Return JSON for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                rendered = render_markdown(new_content)
                return JsonResponse({
                    'success': True,
                    'content': rendered
                })
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
        'all_statuses': get_status_for_entity_type('task'),
        'associated_notes': associated_notes,
        'available_notes': available_notes,
        'is_inbox_epic': is_inbox_epic
    })


def new_task(request, project, epic=None):
    """Create a new task. Epic is optional."""
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
        save_task(project, task_id, metadata, content, epic_id=epic)

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if epic:
                url = reverse('task_detail', kwargs={'project': project, 'epic': epic, 'task': task_id})
            else:
                url = reverse('task_detail_no_epic', kwargs={'project': project, 'task': task_id})
            return JsonResponse({
                'success': True,
                'id': task_id,
                'seq_id': seq_id,
                'title': title,
                'status': status,
                'priority': priority,
                'created': metadata.get('created', ''),
                'due_date': metadata.get('due_date', ''),
                'url': url
            })

        if epic:
            return redirect('task_detail', project=project, epic=epic, task=task_id)
        else:
            return redirect('task_detail_no_epic', project=project, task=task_id)

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = None
    if epic:
        epic_metadata, _ = load_epic(project, epic, metadata_only=True)
        epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic

    return render(request, 'pm/new_task.html', {
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'epic_title': epic_title
    })


def _task_detail_impl(request, project, task, epic=None):
    """Display task details with subtasks and updates. Epic is optional."""
    # Get epic from metadata if not provided
    if epic is None:
        # Try to load task to get epic from metadata
        temp_metadata, _ = load_task(project, task, epic_id=None)
        if temp_metadata:
            epic = temp_metadata.get('epic_id')
    
    metadata, content = load_task(project, task, epic_id=epic)
    if metadata is None:
        raise Http404("Task not found")
    
    # Ensure epic matches metadata (in case it changed)
    epic = metadata.get('epic_id') or epic

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = None
    epic_metadata = None
    if epic:
        epic_metadata, _ = load_epic(project, epic, metadata_only=True)
        epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic
    
    # Check if this task is in the inbox epic
    is_inbox_task = (project == INBOX_PROJECT_ID and epic_metadata and epic_metadata.get('is_inbox_epic', False))
    
    # Helper function for redirects
    def get_task_redirect_url():
        if epic:
            return redirect('task_detail', project=project, epic=epic, task=task)
        else:
            return redirect('task_detail_no_epic', project=project, task=task)

    # Handle checklist operations
    if request.method == 'POST' and handle_checklist_post(request, metadata):
        save_task(project, task, metadata, content, epic_id=epic)
        return get_task_redirect_url()

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

        save_subtask(project, task, subtask_id, subtask_metadata, subtask_content, epic_id=epic)

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

        return get_task_redirect_url()

    # Handle update submission
    if request.method == 'POST' and 'update_content' in request.POST:
        update_content = request.POST.get('update_content', '').strip()
        if update_content:
            # Extract @mentions from update content
            mentions = extract_mentions(update_content)
            if mentions:
                # Ensure persons exist and merge into metadata['people']
                normalized_mentions = ensure_people_exist(mentions)
                current_people = normalize_people(metadata.get('people', []))
                # Merge mentions, avoiding duplicates
                for mention in normalized_mentions:
                    if mention.lower() not in [p.lower() for p in current_people]:
                        current_people.append(mention)
                metadata['people'] = current_people
            
            # Add new update to metadata
            if 'updates' not in metadata:
                metadata['updates'] = []

            metadata['updates'].append({
                'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'content': update_content,
                'type': 'user'
            })

            save_task(project, task, metadata, content, epic_id=epic)

        return get_task_redirect_url()

    # Sort updates newest first - enrich with stored type/activity_type from Update table
    raw_updates = metadata.get('updates', [])
    updates = enrich_updates_with_stored_types(task, raw_updates)
    
    # Parse timestamps
    for u in updates:
        if isinstance(u.get('timestamp'), str):
            try:
                u['timestamp'] = datetime.strptime(u['timestamp'], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                pass
    
    updates.sort(key=lambda x: x['timestamp'] if isinstance(x['timestamp'], datetime) else str(x['timestamp']), reverse=True)

    # Load subtasks
    subtasks = []
    subtask_entities = Subtask.objects.filter(project_id=project, task_id=task)
    if epic:
        subtask_entities = subtask_entities.filter(epic_id=epic)
    else:
        subtask_entities = subtask_entities.filter(epic_id__isnull=True)
    
    for entity in subtask_entities:
        subtasks.append({
            'id': entity.id,
            'seq_id': entity.seq_id or '',
            'title': entity.title or 'Untitled Subtask',
            'status': entity.status_fk.name if entity.status_fk else 'todo',
            'status_display': get_status_display(entity),
            'priority': entity.priority or '',
            'created': entity.created or '',
            'due_date': entity.due_date_dt.isoformat() if entity.due_date_dt else '',
            'order': 0  # Order field not in Entity model, default to 0
        })

    subtasks.sort(key=lambda s: (s.get('order', 0), s.get('title', '')))

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
            save_task(project, task, metadata, content, epic_id=epic)
        return get_task_redirect_url()
    
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
            save_task(project, task, metadata, content, epic_id=epic)
        return get_task_redirect_url()
    
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
            save_task(project, task, metadata, content, epic_id=epic)
        return get_task_redirect_url()
    
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
            save_task(project, task, metadata, content, epic_id=epic)
        return get_task_redirect_url()

    # Handle quick updates (status, priority, schedule)
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        if quick_update == 'status':
            old_status = metadata.get('status', 'todo')
            new_status = request.POST.get('status', old_status)
            if old_status != new_status:
                metadata['status'] = new_status
                add_activity_entry(metadata, 'status_changed', old_status, new_status)
                save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'priority':
            old_priority = metadata.get('priority', '')
            priority = request.POST.get('priority', '').strip()
            if old_priority != priority:
                if priority:
                    metadata['priority'] = priority
                else:
                    metadata.pop('priority', None)
                add_activity_entry(metadata, 'priority_changed', old_priority, priority)
                save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'schedule_start':
            old_start = metadata.get('schedule_start', '')
            new_start = request.POST.get('schedule_start', '')
            if old_start != new_start:
                metadata['schedule_start'] = new_start
                add_activity_entry(metadata, 'schedule_start_changed', old_start, new_start)
                save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'schedule_end':
            old_end = metadata.get('schedule_end', '')
            new_end = request.POST.get('schedule_end', '')
            if old_end != new_end:
                metadata['schedule_end'] = new_end
                add_activity_entry(metadata, 'schedule_end_changed', old_end, new_end)
                save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'due_date':
            old_due = metadata.get('due_date', '')
            new_due = request.POST.get('due_date', '')
            if old_due != new_due:
                metadata['due_date'] = new_due
                add_activity_entry(metadata, 'due_date_changed', old_due, new_due)
                save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'add_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label not in current_labels:
                    current_labels.append(label)
                    metadata['labels'] = current_labels
                    add_activity_entry(metadata, 'label_added', None, label)
                    save_task(project, task, metadata, content, epic_id=epic)
                    cache.delete("all_labels:v1")  # Invalidate cache
            return get_task_redirect_url()
        elif quick_update == 'remove_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label in current_labels:
                    metadata['labels'] = [l for l in current_labels if l != label]
                    add_activity_entry(metadata, 'label_removed', label, None)
                    save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'add_person':
            person = request.POST.get('person', '').strip()
            if person:
                # Ensure person exists (create if needed)
                person_normalized = ensure_person_exists(person)
                current_people = normalize_people(metadata.get('people', []))
                if person_normalized not in current_people:
                    current_people.append(person_normalized)
                    metadata['people'] = current_people
                    add_activity_entry(metadata, 'person_added', None, person_normalized)
                    save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'remove_person':
            person = request.POST.get('person', '').strip()
            if person:
                current_people = normalize_people(metadata.get('people', []))
                if person in current_people:
                    metadata['people'] = [p for p in current_people if p != person]
                    add_activity_entry(metadata, 'person_removed', person, None)
                    save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
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
                    save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
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
                    save_task(project, task, metadata, content, epic_id=epic)
            return get_task_redirect_url()
        elif quick_update == 'description':
            new_content = request.POST.get('description', '').strip()
            content = new_content
            # Extract @mentions from description content
            mentions = extract_mentions(new_content)
            if mentions:
                # Ensure persons exist and merge into metadata['people']
                normalized_mentions = ensure_people_exist(mentions)
                current_people = normalize_people(metadata.get('people', []))
                # Merge mentions, avoiding duplicates
                for mention in normalized_mentions:
                    if mention.lower() not in [p.lower() for p in current_people]:
                        current_people.append(mention)
                metadata['people'] = current_people
            save_task(project, task, metadata, content, epic_id=epic)
            # Return JSON for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                rendered = render_markdown(new_content)
                return JsonResponse({
                    'success': True,
                    'content': rendered
                })
            return get_task_redirect_url()
        elif quick_update == 'edit_update':
            # Edit an existing user update
            update_timestamp = request.POST.get('update_timestamp', '').strip()
            update_content = request.POST.get('update_content', '').strip()
            
            if update_timestamp and update_content and 'updates' in metadata:
                # Find the update by timestamp and ensure it's a user update
                updated = False
                for u in metadata['updates']:
                    if u.get('timestamp') == update_timestamp and u.get('type', 'user') == 'user':
                        u['content'] = update_content
                        updated = True
                        break
                
                if updated:
                    save_task(project, task, metadata, content, epic_id=epic)
                    # Return JSON for AJAX requests
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        rendered = render_markdown(update_content)
                        return JsonResponse({
                            'success': True,
                            'content': rendered
                        })
            
            return get_task_redirect_url()

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
            # Ensure all people exist (create if needed)
            metadata['people'] = ensure_people_exist(people)
        else:
            metadata.pop('people', None)
        content = request.POST.get('content', content)

        save_task(project, task, metadata, content, epic_id=epic)

        return get_task_redirect_url()

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

    # Determine if Actions & Relationships section should default to open
    show_actions_success = request.session.pop('subtask_created_success', False)

    return render(request, 'pm/task_detail.html', {
        'metadata': metadata,
        'content': content,
        'project': project,
        'epic': epic,
        'epic_title': epic_title,
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
        'all_statuses': get_status_for_entity_type('task'),
        'all_subtask_statuses': get_status_for_entity_type('subtask'),
        'blocks': blocks,
        'blocked_by': blocked_by,
        'available_tasks': available_tasks,
        'associated_notes': associated_notes,
        'available_notes': available_notes,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total,
        'subtask_total': subtask_total,
        'subtask_done': subtask_done,
        'subtask_progress': subtask_progress,
        'overall_progress': overall_progress,
        'total_items': total_items,
        'is_inbox_task': is_inbox_task,
        'show_actions_success': show_actions_success
    })


def subtask_detail(request, project, epic, task, subtask):
    """Subtask detail with epic (existing URL pattern)."""
    return _subtask_detail_impl(request, project, task, subtask, epic=epic)


def subtask_detail_no_epic(request, project, task, subtask):
    """Subtask detail without epic (new URL pattern)."""
    return _subtask_detail_impl(request, project, task, subtask, epic=None)


def task_detail(request, project, epic, task):
    """Task detail with epic (existing URL pattern)."""
    return _task_detail_impl(request, project, task, epic=epic)


def task_detail_no_epic(request, project, task):
    """Task detail without epic (new URL pattern)."""
    return _task_detail_impl(request, project, task, epic=None)


def _new_subtask_impl(request, project, task, epic=None):
    """Create a new subtask. Epic is optional."""
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
        save_subtask(project, task, subtask_id, metadata, content, epic_id=epic)

        # Set success flag for session to open Actions & Relationships section
        request.session['subtask_created_success'] = True

        # Return JSON for AJAX requests
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            if epic:
                url = reverse('subtask_detail', kwargs={'project': project, 'epic': epic, 'task': task, 'subtask': subtask_id})
            else:
                url = reverse('subtask_detail_no_epic', kwargs={'project': project, 'task': task, 'subtask': subtask_id})
            return JsonResponse({
                'success': True,
                'id': subtask_id,
                'seq_id': seq_id,
                'title': title,
                'status': status,
                'priority': priority,
                'created': metadata.get('created', ''),
                'due_date': metadata.get('due_date', ''),
                'url': url
            })

        if epic:
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask_id)
        else:
            return redirect('subtask_detail_no_epic', project=project, task=task, subtask=subtask_id)

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = None
    if epic:
        epic_metadata, _ = load_epic(project, epic, metadata_only=True)
        epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic
    task_metadata, _ = load_task(project, task, epic_id=epic, metadata_only=True)
    task_title = task_metadata.get('title', 'Untitled Task') if task_metadata else task

    return render(request, 'pm/new_subtask.html', {
        'project': project,
        'project_title': project_title,
        'epic': epic,
        'epic_title': epic_title,
        'task': task,
        'task_title': task_title
    })


def new_subtask(request, project, epic, task):
    """New subtask with epic (existing URL pattern)."""
    return _new_subtask_impl(request, project, task, epic=epic)


def new_subtask_no_epic(request, project, task):
    """New subtask without epic (new URL pattern)."""
    return _new_subtask_impl(request, project, task, epic=None)


def _subtask_detail_impl(request, project, task, subtask, epic=None):
    """Display subtask details with updates. Epic is optional."""
    # Get epic from metadata if not provided
    if epic is None:
        temp_metadata, _ = load_subtask(project, task, subtask, epic_id=None)
        if temp_metadata:
            epic = temp_metadata.get('epic_id')
    
    metadata, content = load_subtask(project, task, subtask, epic_id=epic)
    if metadata is None:
        raise Http404("Subtask not found")
    
    # Ensure epic matches metadata
    epic = metadata.get('epic_id') or epic

    # Load parent metadata for breadcrumbs
    project_metadata, _ = load_project(project, metadata_only=True)
    project_title = project_metadata.get('title', 'Untitled Project') if project_metadata else project
    epic_title = None
    epic_metadata = None
    if epic:
        epic_metadata, _ = load_epic(project, epic, metadata_only=True)
        epic_title = epic_metadata.get('title', 'Untitled Epic') if epic_metadata else epic
    task_metadata, _ = load_task(project, task, epic_id=epic, metadata_only=True)
    task_title = task_metadata.get('title', 'Untitled Task') if task_metadata else task
    
    # Helper function for redirects
    def get_subtask_redirect_url():
        if epic:
            return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)
        else:
            return redirect('subtask_detail_no_epic', project=project, task=task, subtask=subtask)

    # Handle checklist operations
    if request.method == 'POST' and handle_checklist_post(request, metadata):
        save_subtask(project, task, subtask, metadata, content, epic_id=epic)
        return get_subtask_redirect_url()

    # Calculate progress
    _, markdown_total, markdown_progress = calculate_markdown_progress(content)
    _, checklist_total, checklist_progress = calculate_checklist_progress(metadata)

    # Handle update submission
    if request.method == 'POST' and 'update_content' in request.POST:
        update_content = request.POST.get('update_content', '').strip()
        if update_content:
            # Extract @mentions from update content
            mentions = extract_mentions(update_content)
            if mentions:
                # Ensure persons exist and merge into metadata['people']
                normalized_mentions = ensure_people_exist(mentions)
                current_people = normalize_people(metadata.get('people', []))
                # Merge mentions, avoiding duplicates
                for mention in normalized_mentions:
                    if mention.lower() not in [p.lower() for p in current_people]:
                        current_people.append(mention)
                metadata['people'] = current_people
            
            # Add new update to metadata
            if 'updates' not in metadata:
                metadata['updates'] = []

            metadata['updates'].append({
                'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
                'content': update_content,
                'type': 'user'
            })

            save_subtask(project, task, subtask, metadata, content, epic_id=epic)

        return redirect('subtask_detail', project=project, epic=epic, task=task, subtask=subtask)

    # Sort updates newest first - enrich with stored type/activity_type from Update table
    raw_updates = metadata.get('updates', [])
    updates = enrich_updates_with_stored_types(subtask, raw_updates)
    
    # Parse timestamps
    for u in updates:
        if isinstance(u.get('timestamp'), str):
            try:
                u['timestamp'] = datetime.strptime(u['timestamp'], '%Y-%m-%dT%H:%M:%S')
            except ValueError:
                pass
    
    updates.sort(key=lambda x: x['timestamp'] if isinstance(x['timestamp'], datetime) else str(x['timestamp']), reverse=True)

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
            save_subtask(project, task, subtask, metadata, content, epic_id=epic)
        return get_subtask_redirect_url()
    
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
            save_subtask(project, task, subtask, metadata, content, epic_id=epic)
        return get_subtask_redirect_url()
    
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
            save_subtask(project, task, subtask, metadata, content, epic_id=epic)
        return get_subtask_redirect_url()
    
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
            save_subtask(project, task, subtask, metadata, content, epic_id=epic)
        return get_subtask_redirect_url()

    # Handle quick updates (status, priority, schedule)
    if request.method == 'POST' and 'quick_update' in request.POST:
        quick_update = request.POST.get('quick_update')
        if quick_update == 'status':
            old_status = metadata.get('status', 'todo')
            new_status = request.POST.get('status', old_status)
            if old_status != new_status:
                metadata['status'] = new_status
                add_activity_entry(metadata, 'status_changed', old_status, new_status)
                save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'priority':
            old_priority = metadata.get('priority', '')
            priority = request.POST.get('priority', '').strip()
            if old_priority != priority:
                if priority:
                    metadata['priority'] = priority
                else:
                    metadata.pop('priority', None)
                add_activity_entry(metadata, 'priority_changed', old_priority, priority)
                save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'schedule_start':
            old_start = metadata.get('schedule_start', '')
            new_start = request.POST.get('schedule_start', '')
            if old_start != new_start:
                metadata['schedule_start'] = new_start
                add_activity_entry(metadata, 'schedule_start_changed', old_start, new_start)
                save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'schedule_end':
            old_end = metadata.get('schedule_end', '')
            new_end = request.POST.get('schedule_end', '')
            if old_end != new_end:
                metadata['schedule_end'] = new_end
                add_activity_entry(metadata, 'schedule_end_changed', old_end, new_end)
                save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'due_date':
            old_due = metadata.get('due_date', '')
            new_due = request.POST.get('due_date', '')
            if old_due != new_due:
                metadata['due_date'] = new_due
                add_activity_entry(metadata, 'due_date_changed', old_due, new_due)
                save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'add_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label not in current_labels:
                    current_labels.append(label)
                    metadata['labels'] = current_labels
                    add_activity_entry(metadata, 'label_added', None, label)
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
                    cache.delete("all_labels:v1")  # Invalidate cache
            return get_subtask_redirect_url()
        elif quick_update == 'remove_label':
            label = request.POST.get('label', '').strip()
            if label:
                current_labels = normalize_labels(metadata.get('labels', []))
                if label in current_labels:
                    metadata['labels'] = [l for l in current_labels if l != label]
                    add_activity_entry(metadata, 'label_removed', label, None)
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'add_person':
            person = request.POST.get('person', '').strip()
            if person:
                # Ensure person exists (create if needed)
                person_normalized = ensure_person_exists(person)
                current_people = normalize_people(metadata.get('people', []))
                if person_normalized not in current_people:
                    current_people.append(person_normalized)
                    metadata['people'] = current_people
                    add_activity_entry(metadata, 'person_added', None, person_normalized)
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'remove_person':
            person = request.POST.get('person', '').strip()
            if person:
                current_people = normalize_people(metadata.get('people', []))
                if person in current_people:
                    metadata['people'] = [p for p in current_people if p != person]
                    add_activity_entry(metadata, 'person_removed', person, None)
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
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
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
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
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            return get_subtask_redirect_url()
        elif quick_update == 'description':
            new_content = request.POST.get('description', '').strip()
            content = new_content
            # Extract @mentions from description content
            mentions = extract_mentions(new_content)
            if mentions:
                # Ensure persons exist and merge into metadata['people']
                normalized_mentions = ensure_people_exist(mentions)
                current_people = normalize_people(metadata.get('people', []))
                # Merge mentions, avoiding duplicates
                for mention in normalized_mentions:
                    if mention.lower() not in [p.lower() for p in current_people]:
                        current_people.append(mention)
                metadata['people'] = current_people
            save_subtask(project, task, subtask, metadata, content, epic_id=epic)
            # Return JSON for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                rendered = render_markdown(new_content)
                return JsonResponse({
                    'success': True,
                    'content': rendered
                })
            return get_subtask_redirect_url()
        elif quick_update == 'edit_update':
            # Edit an existing user update
            update_timestamp = request.POST.get('update_timestamp', '').strip()
            update_content = request.POST.get('update_content', '').strip()
            
            if update_timestamp and update_content and 'updates' in metadata:
                # Find the update by timestamp and ensure it's a user update
                updated = False
                for u in metadata['updates']:
                    if u.get('timestamp') == update_timestamp and u.get('type', 'user') == 'user':
                        u['content'] = update_content
                        updated = True
                        break
                
                if updated:
                    save_subtask(project, task, subtask, metadata, content, epic_id=epic)
                    # Return JSON for AJAX requests
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        rendered = render_markdown(update_content)
                        return JsonResponse({
                            'success': True,
                            'content': rendered
                        })
            
            return get_subtask_redirect_url()

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

        save_subtask(project, task, subtask, metadata, content, epic_id=epic)

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

    # Determine if Actions & Relationships section should default to open
    show_actions_success = request.session.pop('subtask_created_success', False)

    return render(request, 'pm/subtask_detail.html', {
        'metadata': metadata,
        'content': content,
        'project': project,
        'epic': epic,
        'epic_title': epic_title,
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
        'all_statuses': get_status_for_entity_type('subtask'),
        'blocks': blocks,
        'blocked_by': blocked_by,
        'available_tasks': available_tasks,
        'associated_notes': associated_notes,
        'available_notes': available_notes,
        'markdown_progress': markdown_progress,
        'markdown_total': markdown_total,
        'checklist_progress': checklist_progress,
        'checklist_total': checklist_total,
        'show_actions_success': show_actions_success
    })


def get_all_scheduled_tasks():
    """Helper to find all tasks with a schedule across all projects."""
    cache_key = "scheduled_tasks:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    scheduled_tasks = []
    
    # Query all tasks with schedule_start or schedule_end
    tasks = Task.objects.select_related('status_fk', 'project', 'epic').exclude(
        schedule_start='', schedule_end=''
    ).exclude(schedule_start__isnull=True, schedule_end__isnull=True)
    
    for task in tasks:
        if task.schedule_start_dt or task.schedule_end_dt:
            # Get project color
            try:
                project = Project.objects.get(id=task.project_id)
                project_color = get_project_color(task.project_id, project.color)
            except Entity.DoesNotExist:
                project_color = get_project_color(task.project_id, None)
                    
            task_status = task.status_fk.name if task.status_fk else 'todo'
            scheduled_tasks.append({
                'id': task.id,
                'project_id': task.project_id,
                'epic_id': task.epic_id,
                'title': task.title or 'Untitled Task',
                'seq_id': task.seq_id or '',
                'status': task_status,
                'status_display': get_status_display(task),
                'schedule_start': task.schedule_start_dt.isoformat() if task.schedule_start_dt else '',
                'schedule_end': task.schedule_end_dt.isoformat() if task.schedule_end_dt else '',
                'project_color': project_color,
                'project_color_bg': hex_to_rgba(project_color, 0.15)
            })
        
    cache.set(cache_key, scheduled_tasks, 30)
    return scheduled_tasks


def get_all_projects_hierarchy():
    """Get all projects with their epics, tasks, and subtasks for the sidebar."""
    cache_key = "projects_hierarchy:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    projects = []
    project_entities = Project.objects.all()
    
    for project in project_entities:
        project_data = {
            'id': project.id,
            'title': project.title or 'Untitled Project',
            'epics': []
        }
        
        # Load epics for this project
        epics = Epic.objects.filter(project_id=project.id).order_by('id')
        for epic in epics:
            epic_data = {
                'id': epic.id,
                'title': epic.title or 'Untitled Epic',
                'seq_id': epic.seq_id or '',
                'tasks': []
            }
            
            # Load tasks for this epic
            tasks = Task.objects.filter(project_id=project.id, epic_id=epic.id)
            for task in tasks:
                task_data = {
                    'id': task.id,
                    'title': task.title or 'Untitled Task',
                    'seq_id': task.seq_id or '',
                    'status': task.status_fk.name if task.status_fk else 'todo',
                    'subtasks': []
                }
                
                # Load subtasks for this task
                subtasks = Subtask.objects.filter(project_id=project.id, task_id=task.id, epic_id=epic.id)
                for subtask in subtasks:
                    task_data['subtasks'].append({
                        'id': subtask.id,
                        'title': subtask.title or 'Untitled Subtask',
                        'status': subtask.status_fk.name if subtask.status_fk else 'todo'
                    })
                
                epic_data['tasks'].append(task_data)
            
            project_data['epics'].append(epic_data)
        
        # Load tasks directly under project (without epic)
        direct_tasks_entities = Task.objects.filter(project_id=project.id, epic__isnull=True)
        direct_tasks = []
        for task in direct_tasks_entities:
            task_data = {
                'id': task.id,
                'title': task.title or 'Untitled Task',
                'seq_id': task.seq_id or '',
                'status': task.status_fk.name if task.status_fk else 'todo',
                'subtasks': []
            }
            
            # Load subtasks for direct tasks
            subtasks = Subtask.objects.filter(project_id=project.id, task_id=task.id, epic__isnull=True)
            for subtask in subtasks:
                task_data['subtasks'].append({
                    'id': subtask.id,
                    'title': subtask.title or 'Untitled Subtask',
                    'status': subtask.status_fk.name if subtask.status_fk else 'todo'
                })
            
            direct_tasks.append(task_data)
        
        if direct_tasks:
            # Add direct tasks as a special "epic" with None ID
            project_data['epics'].append({
                'id': None,
                'title': 'Direct Tasks',
                'seq_id': '',
                'tasks': direct_tasks
            })
        
        projects.append(project_data)
    
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
    cache_key = "work_items:v3"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    items = []

    # Query all tasks with project and epic data
    tasks = Task.objects.all().select_related('status_fk')
    for task in tasks:
        # Get project title
        project_title = ''
        if task.project_id:
            try:
                project_entity = Project.objects.get(id=task.project_id)
                project_title = project_entity.title or task.project_id
            except Entity.DoesNotExist:
                project_title = task.project_id
        
        # Get epic title
        epic_title = ''
        if task.epic_id:
            try:
                epic_entity = Epic.objects.get(id=task.epic_id)
                epic_title = epic_entity.title or task.epic_id
            except Entity.DoesNotExist:
                epic_title = task.epic_id
        
        items.append({
            'type': 'task',
            'id': task.id,
            'title': task.title or 'Untitled Task',
            'status': task.status_fk.name if task.status_fk else 'todo',
            'status_display': task.status_fk.display_name if task.status_fk else 'Todo',
            'priority': task.priority or '',
            'due_date': task.due_date_dt.isoformat() if task.due_date_dt else '',
            'project_id': task.project_id,
            'project_title': project_title,
            'epic_id': task.epic_id,
            'epic_title': epic_title,
        })

    # Query all subtasks with project and epic data
    subtasks = Subtask.objects.all().select_related('status_fk')
    for subtask in subtasks:
        # Get project title
        project_title = ''
        if subtask.project_id:
            try:
                project_entity = Project.objects.get(id=subtask.project_id)
                project_title = project_entity.title or subtask.project_id
            except Entity.DoesNotExist:
                project_title = subtask.project_id
        
        # Get epic title
        epic_title = ''
        if subtask.epic_id:
            try:
                epic_entity = Epic.objects.get(id=subtask.epic_id)
                epic_title = epic_entity.title or subtask.epic_id
            except Entity.DoesNotExist:
                epic_title = subtask.epic_id
        
        items.append({
            'type': 'subtask',
            'id': subtask.id,
            'seq_id': subtask.seq_id or '',
            'title': subtask.title or 'Untitled Subtask',
            'status': subtask.status_fk.name if subtask.status_fk else 'todo',
            'status_display': subtask.status_fk.display_name if subtask.status_fk else 'Todo',
            'priority': subtask.priority or '',
            'due_date': subtask.due_date_dt.isoformat() if subtask.due_date_dt else '',
            'project_id': subtask.project_id,
            'project_title': project_title,
            'epic_id': subtask.epic_id,
            'epic_title': epic_title,
            'task_id': subtask.task_id,
        })

    cache.set(cache_key, items, 30)
    return items


def find_entity_in_project(project_id, entity_id):
    """Find a task or subtask by ID within a project.
    
    Returns a dict with 'type', 'epic_id', 'task_id' (if subtask), and path info,
    or None if not found.
    """
    # Check if it's a task
    try:
        task = Task.objects.get(id=entity_id, project_id=project_id)
        return {
            'type': 'task',
            'epic_id': task.epic_id,
            'task_id': task.id
        }
    except Entity.DoesNotExist:
        pass
    
    # Check if it's a subtask
    try:
        subtask = Subtask.objects.get(id=entity_id, project_id=project_id)
        return {
            'type': 'subtask',
            'epic_id': subtask.epic_id,
            'task_id': subtask.task_id,
            'subtask_id': subtask.id
        }
    except Entity.DoesNotExist:
        pass
    
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
        metadata, content = load_task(project_id, target_info['task_id'], epic_id=target_info.get('epic_id'))
        if metadata is None:
            return
        
        if reciprocal not in metadata:
            metadata[reciprocal] = []
        
        if action == 'add':
            if source_id not in metadata[reciprocal]:
                metadata[reciprocal].append(source_id)
        elif action == 'remove':
            metadata[reciprocal] = [x for x in metadata[reciprocal] if x != source_id]
        
        save_task(project_id, target_info['task_id'], metadata, content, epic_id=target_info.get('epic_id'))
    
    elif target_info['type'] == 'subtask':
        metadata, content = load_subtask(project_id, target_info['task_id'], 
                                          target_info['subtask_id'], epic_id=target_info.get('epic_id'))
        if metadata is None:
            return
        
        if reciprocal not in metadata:
            metadata[reciprocal] = []
        
        if action == 'add':
            if source_id not in metadata[reciprocal]:
                metadata[reciprocal].append(source_id)
        elif action == 'remove':
            metadata[reciprocal] = [x for x in metadata[reciprocal] if x != source_id]
        
        save_subtask(project_id, target_info['task_id'], 
                     target_info['subtask_id'], metadata, content, epic_id=target_info.get('epic_id'))


def get_project_tasks_for_dependencies(project_id, exclude_task_id=None, exclude_subtask_id=None):
    """Get all tasks and subtasks in a project for dependency selection."""
    tasks_list = []
    
    # Cache epic titles
    epic_titles = {}
    epics = Epic.objects.filter(project_id=project_id)
    for epic in epics:
        epic_titles[epic.id] = epic.title or 'Untitled Epic'
    
    # Query all tasks in the project (with and without epic)
    tasks = Task.objects.select_related('status_fk', 'project', 'epic').filter(project_id=project_id)
    for task in tasks:
        if exclude_task_id and task.id == exclude_task_id:
            continue
        
        task_status = task.status_fk.name if task.status_fk else 'todo'
        tasks_list.append({
            'type': 'task',
            'id': task.id,
            'epic_id': task.epic_id,
            'epic_title': epic_titles.get(task.epic_id, 'Untitled Epic') if task.epic_id else None,
            'seq_id': task.seq_id or '',
            'title': task.title or 'Untitled Task',
            'status': task_status,
            'status_display': get_status_display(task),
            'priority': task.priority or ''
        })
        
        task_title = task.title or 'Untitled Task'
        task_seq = task.seq_id or ''
        
        # Query subtasks for this task
        subtasks = Subtask.objects.select_related('status_fk').filter(project_id=project_id, task_id=task.id)
        for subtask in subtasks:
            if exclude_subtask_id and subtask.id == exclude_subtask_id:
                continue
            
            subtask_status = subtask.status_fk.name if subtask.status_fk else 'todo'
            tasks_list.append({
                'type': 'subtask',
                'id': subtask.id,
                'seq_id': subtask.seq_id or '',
                'task_id': task.id,
                'task_title': task_title,
                'task_seq_id': task_seq,
                'epic_id': subtask.epic_id,
                'epic_title': epic_titles.get(subtask.epic_id, 'Untitled Epic') if subtask.epic_id else None,
                'title': subtask.title or 'Untitled Subtask',
                'status': subtask_status,
                'status_display': get_status_display(subtask),
                'priority': subtask.priority or ''
            })
    
    # Sort by seq_id
    tasks_list.sort(key=lambda x: (x.get('seq_id', 'z999'), x.get('title', '')))
    
    return tasks_list


def get_project_activity(project_id):
    """Get recent activity (updates and system messages) across epics, tasks and subtasks in a project."""
    cache_key = f"activity:{project_id}:v4"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    activity = []

    def _derive_update_type(update_obj):
        """Infer update type when older rows lack explicit type/activity_type."""
        inferred = getattr(update_obj, 'type', None)
        if inferred:
            return inferred
        activity_type = getattr(update_obj, 'activity_type', None)
        if activity_type:
            return 'system'
        content = (update_obj.content or '').lower()
        system_prefixes = [
            'status changed', 'priority changed', 'start time', 'end time', 'due date',
            'label "', "label '", 'person "', "person '", 'note "', "note '", 'dependency '
        ]
        for prefix in system_prefixes:
            if content.startswith(prefix):
                return 'system'
        return 'user'
    
    # Query project-level updates first
    project_updates = Update.objects.filter(entity_id=project_id).order_by('timestamp')
    for u in project_updates:
        ts = u.timestamp
        try:
            ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
        except ValueError:
            ts_dt = ts
        update_type = _derive_update_type(u)
        activity.append({
            'type': 'project',
            'entity_type': 'project',
            'title': 'Project',
            'content': u.content,
            'update_type': update_type,
            'activity_type': getattr(u, 'activity_type', None),
            'timestamp': ts_dt,
            'url': reverse('project_detail', kwargs={'project': project_id})
        })
    
    # Query all epics in the project
    epics = Epic.objects.filter(project_id=project_id)
    for epic in epics:
        # Get updates from Update table
        updates = Update.objects.filter(entity_id=epic.id).order_by('timestamp')
        for u in updates:
            ts = u.timestamp
            try:
                ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
            except ValueError:
                ts_dt = ts
            update_type = _derive_update_type(u)
            activity.append({
                'type': 'epic',
                'entity_type': 'epic',
                'title': epic.title or 'Untitled Epic',
                'content': u.content,
                'update_type': update_type,
                'activity_type': getattr(u, 'activity_type', None),
                'timestamp': ts_dt,
                'url': reverse('epic_detail', kwargs={'project': project_id, 'epic': epic.id})
            })
    
    # Query all tasks in the project (with and without epic)
    tasks = Task.objects.filter(project_id=project_id)
    for task in tasks:
        # Get updates from Update table
        updates = Update.objects.filter(entity_id=task.id).order_by('timestamp')
        for u in updates:
            ts = u.timestamp
            try:
                ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
            except ValueError:
                ts_dt = ts
            update_type = _derive_update_type(u)
            
            if task.epic_id:
                url = reverse('task_detail', kwargs={'project': project_id, 'epic': task.epic_id, 'task': task.id})
            else:
                url = reverse('task_detail_no_epic', kwargs={'project': project_id, 'task': task.id})
            
            activity.append({
                'type': 'task',
                'entity_type': 'task',
                'title': task.title or 'Untitled Task',
                'content': u.content,
                'update_type': update_type,
                'activity_type': getattr(u, 'activity_type', None),
                'timestamp': ts_dt,
                'url': url
            })
    
    # Query all subtasks in the project
    subtasks = Subtask.objects.filter(project_id=project_id)
    for subtask in subtasks:
        # Get updates from Update table
        updates = Update.objects.filter(entity_id=subtask.id).order_by('timestamp')
        for u in updates:
            ts = u.timestamp
            try:
                ts_dt = datetime.strptime(ts, '%Y-%m-%dT%H:%M:%S') if isinstance(ts, str) else ts
            except ValueError:
                ts_dt = ts
            update_type = _derive_update_type(u)
            
            if subtask.epic_id:
                url = reverse('subtask_detail', kwargs={'project': project_id, 'epic': subtask.epic_id, 'task': subtask.task_id, 'subtask': subtask.id})
            else:
                url = reverse('subtask_detail_no_epic', kwargs={'project': project_id, 'task': subtask.task_id, 'subtask': subtask.id})
            
            activity.append({
                'type': 'subtask',
                'entity_type': 'subtask',
                'title': subtask.title or 'Untitled Subtask',
                'content': u.content,
                'update_type': update_type,
                'activity_type': getattr(u, 'activity_type', None),
                'timestamp': ts_dt,
                'url': url
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
        
        if not all([project_id, task_id, schedule_start]):
            return JsonResponse({'error': 'Missing required parameters'}, status=400)
        
        # Epic is optional
        epic_id = request.POST.get('epic_id') or None
        
        # Validate IDs
        if not is_valid_project_id(project_id) or not validate_id(task_id, 'task'):
            return JsonResponse({'error': 'Invalid IDs'}, status=400)
        
        if epic_id and not validate_id(epic_id, 'epic'):
            epic_id = None
        
        if subtask_id:
            # Update subtask
            if not validate_id(subtask_id, 'subtask'):
                return JsonResponse({'error': 'Invalid subtask ID'}, status=400)
            
            metadata, content = load_subtask(project_id, task_id, subtask_id, epic_id=epic_id)
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
            
            save_subtask(project_id, task_id, subtask_id, metadata, content, epic_id=epic_id)
        else:
            # Update task
            metadata, content = load_task(project_id, task_id, epic_id=epic_id)
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
            
            save_task(project_id, task_id, metadata, content, epic_id=epic_id)
        
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
                meta, content = load_task(project_id, t_id, epic_id=epic_id)
                if meta is None:
                    continue
                meta['order'] = idx
                save_task(project_id, t_id, meta, content, epic_id=epic_id)
        elif item_type == 'subtask':
            if not task_id or not validate_id(task_id, 'task'):
                return JsonResponse({'error': 'Invalid task ID'}, status=400)
            for idx, s_id in enumerate(ids):
                if not validate_id(s_id, 'subtask'):
                    continue
                meta, content = load_subtask(project_id, task_id, s_id, epic_id=epic_id)
                if meta is None:
                    continue
                meta['order'] = idx
                save_subtask(project_id, task_id, s_id, meta, content, epic_id=epic_id)
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

    if not all([item_type, project_id, status]):
        return JsonResponse({'error': 'Missing parameters'}, status=400)

    # Epic is optional
    epic_id = request.POST.get('epic_id') or None

    if not is_valid_project_id(project_id):
        return JsonResponse({'error': 'Invalid project ID'}, status=400)

    if epic_id and not validate_id(epic_id, 'epic'):
        epic_id = None

    if status not in ['todo', 'in_progress', 'done', 'on_hold', 'blocked', 'cancelled', 'next']:
        return JsonResponse({'error': 'Invalid status'}, status=400)

    try:
        if item_type == 'task':
            if not validate_id(task_id, 'task'):
                return JsonResponse({'error': 'Invalid task ID'}, status=400)
            meta, content = load_task(project_id, task_id, epic_id=epic_id)
            if meta is None:
                return JsonResponse({'error': 'Task not found'}, status=404)
            meta['status'] = status
            save_task(project_id, task_id, meta, content, epic_id=epic_id)
        elif item_type == 'subtask':
            if not task_id or not validate_id(task_id, 'task') or not validate_id(subtask_id, 'subtask'):
                return JsonResponse({'error': 'Invalid IDs'}, status=400)
            meta, content = load_subtask(project_id, task_id, subtask_id, epic_id=epic_id)
            if meta is None:
                return JsonResponse({'error': 'Subtask not found'}, status=404)
            meta['status'] = status
            save_subtask(project_id, task_id, subtask_id, meta, content, epic_id=epic_id)
        else:
            return JsonResponse({'error': 'Invalid type'}, status=400)
    except Exception as e:
        logger.error(f"Error updating status: {e}")
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'success': True})


def bulk_update_items(request):
    """Handle bulk updates from form POST - groups items server-side."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    # Handle two different input formats:
    # 1. From my_work/today: items_data, status, priority, due_date
    # 2. From task_detail/epic_detail: ids, actions, type, project_id, epic_id, task_id
    
    items_data_str = request.POST.get('items_data', '')
    
    # Check if this is the AJAX format from task_detail/epic_detail
    if not items_data_str and request.POST.get('ids'):
        # AJAX format from task/epic detail pages
        ids = request.POST.get('ids', '').strip()
        item_type = request.POST.get('type', '').strip()
        project_id = request.POST.get('project_id', '').strip()
        epic_id = request.POST.get('epic_id', '').strip()
        task_id = request.POST.get('task_id', '').strip()
        actions_str = request.POST.get('actions', '')
        
        if not ids:
            return JsonResponse({'error': 'No items selected'}, status=400)
        
        try:
            actions = json.loads(actions_str) if actions_str else []
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid actions format'}, status=400)
        
        if not actions:
            return JsonResponse({'error': 'No actions specified'}, status=400)
        
        # Convert to items_data format
        items_data = []
        for item_id in ids.split(','):
            if item_id.strip():
                items_data.append({
                    'item_id': item_id.strip(),
                    'project_id': project_id,
                    'item_type': item_type,
                    'task_id': task_id if item_type == 'subtask' else ''
                })
    else:
        # Form POST format from my_work/today
        if not items_data_str:
            return JsonResponse({'error': 'No items selected'}, status=400)
        
        status = request.POST.get('status', '').strip()
        priority = request.POST.get('priority', '').strip()
        due_date = request.POST.get('due_date', '').strip()
        
        if not (status or priority or due_date):
            return JsonResponse({'error': 'No actions specified'}, status=400)
        
        # Parse items data
        items_data = []
        for item_str in items_data_str.split(','):
            parts = item_str.strip().split('|')
            if len(parts) < 3:
                continue
            items_data.append({
                'item_id': parts[0],
                'project_id': parts[1],
                'item_type': parts[2],
                'task_id': parts[3] if len(parts) > 3 else ''
            })
        
        # Build actions array
        actions = []
        if status:
            actions.append({'type': 'status', 'value': status})
        if priority:
            actions.append({'type': 'priority', 'value': priority})
        if due_date:
            actions.append({'type': 'due_date', 'value': due_date})
    
    # Validate actions
    valid_statuses = ['todo', 'in_progress', 'done', 'on_hold', 'blocked', 'cancelled', 'next']
    valid_priorities = ['', '1', '2', '3', '4', '5']
    
    for action in actions:
        action_type = action['type']
        action_value = action['value']
        
        if action_type == 'status' and action_value not in valid_statuses:
            return JsonResponse({'error': f'Invalid status: {action_value}'}, status=400)
        if action_type == 'priority' and action_value not in valid_priorities:
            return JsonResponse({'error': f'Invalid priority: {action_value}'}, status=400)
        if action_type == 'due_date' and action_value and not re.match(r'^\d{4}-\d{2}-\d{2}$', action_value):
            return JsonResponse({'error': 'Invalid date format'}, status=400)
    
    # Group items by (project_id, item_type, task_id)
    groups = {}
    for item in items_data:
        key = (item['project_id'], item['item_type'], item['task_id'])
        if key not in groups:
            groups[key] = {
                'project_id': item['project_id'],
                'item_type': item['item_type'],
                'task_id': item['task_id'],
                'item_ids': []
            }
        groups[key]['item_ids'].append(item['item_id'])
    
    # Process each group
    updated = 0
    try:
        for group_key, group in groups.items():
            project_id = group['project_id']
            item_type = group['item_type']
            task_id = group['task_id']
            item_ids = group['item_ids']
            
            # Validate project
            if not is_valid_project_id(project_id):
                continue
            
            if item_type == 'task':
                for item_id in item_ids:
                    if not validate_id(item_id, 'task'):
                        continue
                    
                    try:
                        task_entity = Task.objects.get(id=item_id, project_id=project_id)
                        actual_epic_id = task_entity.epic_id if task_entity.epic_id else None
                    except Entity.DoesNotExist:
                        continue
                    
                    # Load and update task
                    meta, content = load_task(project_id, item_id, epic_id=actual_epic_id)
                    if meta is None:
                        continue
                    
                    # Apply actions
                    for action in actions:
                        action_type = action['type']
                        action_value = action['value']
                        
                        if action_type == 'status':
                            old_status = meta.get('status', 'todo')
                            if old_status != action_value:
                                meta['status'] = action_value
                                add_activity_entry(meta, 'status_changed', old_status, action_value)
                        elif action_type == 'priority':
                            old_priority = meta.get('priority', '')
                            if old_priority != action_value:
                                if action_value:
                                    meta['priority'] = action_value
                                elif 'priority' in meta:
                                    del meta['priority']
                                add_activity_entry(meta, 'priority_changed', old_priority, action_value)
                        elif action_type == 'due_date':
                            old_due_date = meta.get('due_date', '')
                            if old_due_date != action_value:
                                if action_value:
                                    meta['due_date'] = action_value
                                elif 'due_date' in meta:
                                    del meta['due_date']
                                add_activity_entry(meta, 'due_date_changed', old_due_date, action_value)
                    
                    save_task(project_id, item_id, meta, content, epic_id=actual_epic_id)
                    updated += 1
            
            elif item_type == 'subtask':
                if not task_id or not validate_id(task_id, 'task'):
                    continue
                
                for item_id in item_ids:
                    if not validate_id(item_id, 'subtask'):
                        continue
                    
                    try:
                        subtask_entity = Subtask.objects.get(id=item_id, project_id=project_id, task_id=task_id)
                        actual_epic_id = subtask_entity.epic_id if subtask_entity.epic_id else None
                    except Entity.DoesNotExist:
                        continue
                    
                    # Load and update subtask
                    meta, content = load_subtask(project_id, task_id, item_id, epic_id=actual_epic_id)
                    if meta is None:
                        continue
                    
                    # Apply actions
                    for action in actions:
                        action_type = action['type']
                        action_value = action['value']
                        
                        if action_type == 'status':
                            old_status = meta.get('status', 'todo')
                            if old_status != action_value:
                                meta['status'] = action_value
                                add_activity_entry(meta, 'status_changed', old_status, action_value)
                        elif action_type == 'priority':
                            old_priority = meta.get('priority', '')
                            if old_priority != action_value:
                                if action_value:
                                    meta['priority'] = action_value
                                elif 'priority' in meta:
                                    del meta['priority']
                                add_activity_entry(meta, 'priority_changed', old_priority, action_value)
                        elif action_type == 'due_date':
                            old_due_date = meta.get('due_date', '')
                            if old_due_date != action_value:
                                if action_value:
                                    meta['due_date'] = action_value
                                elif 'due_date' in meta:
                                    del meta['due_date']
                                add_activity_entry(meta, 'due_date_changed', old_due_date, action_value)
                    
                    save_subtask(project_id, task_id, item_id, meta, content, epic_id=actual_epic_id)
                    updated += 1
        
        # Update project stats
        for group_key in groups.keys():
            project_id = group_key[0]
            update_project_stats(project_id)
        
        # Invalidate work items cache so work views show fresh data
        cache.delete('work_items:v3')
        
        # Check if this is an AJAX request (from task_detail/epic_detail)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.POST.get('ids')
        
        if is_ajax:
            # Return JSON response for AJAX requests
            return JsonResponse({
                'success': True,
                'updated': updated,
                'message': f'Updated {updated} item{"s" if updated != 1 else ""}'
            })
        else:
            # Redirect back to the referring page with success message for form submissions
            messages.success(request, f'Updated {updated} item{"s" if updated != 1 else ""}')
            
            # Determine where to redirect based on the referrer
            referrer = request.META.get('HTTP_REFERER', '')
            if 'today' in referrer:
                return redirect('today')
            else:
                return redirect('my_work')
    
    except Exception as e:
        logger.error(f"Error in bulk update: {e}")
        return JsonResponse({'error': str(e)}, status=500)


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
    on_hold_items = []
    blocked_items = []
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

        if status == 'todo':
            open_items.append(item)
        if status in ['in_progress', 'next']:
            in_progress.append(item)
        if status == 'on_hold':
            on_hold_items.append(item)
        if status == 'blocked':
            blocked_items.append(item)

        if due:
            if due < today and status not in ['done', 'blocked', 'cancelled', 'on_hold']:
                overdue.append(item)
            elif today <= due <= due_soon_cutoff and status not in ['done', 'blocked', 'cancelled', 'on_hold']:
                due_soon.append(item)

    # Default sort by priority (lower number = higher priority)
    def sort_by_priority(items_list):
        return sorted(items_list, key=lambda x: (x.get('priority') or '999', x.get('title', '')))
    
    open_items = sort_by_priority(open_items)
    in_progress = sort_by_priority(in_progress)
    on_hold_items = sort_by_priority(on_hold_items)
    blocked_items = sort_by_priority(blocked_items)
    due_soon = sort_by_priority(due_soon)
    overdue = sort_by_priority(overdue)

    return render(request, 'pm/my_work.html', {
        'open_items': open_items,
        'in_progress': in_progress,
        'on_hold_items': on_hold_items,
        'due_soon': due_soon,
        'overdue': overdue,
        'blocked_items': blocked_items,
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
    """Today, In Progress, and Next view."""
    items = get_all_work_items()
    today = date.today()

    today_items = []
    in_progress_items = []
    next_items = []

    for item in items:
        status = item.get('status')
        if status == 'done':
            continue

        due = parse_date_safe(item.get('due_date', ''))
        if due == today:
            today_items.append(item)
        elif status == 'in_progress':
            in_progress_items.append(item)
        elif status == 'next':
            next_items.append(item)

    # Default sort by priority (lower number = higher priority)
    def sort_by_priority(items_list):
        return sorted(items_list, key=lambda x: (x.get('priority') or '999', x.get('title', '')))
    
    today_items = sort_by_priority(today_items)
    in_progress_items = sort_by_priority(in_progress_items)
    next_items = sort_by_priority(next_items)

    return render(request, 'pm/today.html', {
        'today_items': today_items,
        'in_progress_items': in_progress_items,
        'next_items': next_items,
    })


def kanban_view(request, project=None, epic=None):
    """Kanban board view with drag-and-drop."""
    if project and epic:
        # Epic-specific kanban
        epic_metadata, _ = load_epic(project, epic, metadata_only=True)
        if epic_metadata is None:
            raise Http404("Epic not found")
        
        items = []
        tasks = Task.objects.select_related('status_fk').filter(project_id=project, epic_id=epic)
        for task in tasks:
            if not task.archived:
                task_status = task.status_fk.name if task.status_fk else 'todo'
                items.append({
                    'type': 'task',
                    'id': task.id,
                    'title': task.title or 'Untitled Task',
                    'status': task_status,
                    'status_display': get_status_display(task),
                    'priority': task.priority or '',
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
        epics = Epic.objects.filter(project_id=project)
        for epic_entity in epics:
            if epic_entity.archived:
                continue
            
            epic_title = epic_entity.title or 'Untitled Epic'
            tasks = Task.objects.select_related('status_fk').filter(project_id=project, epic_id=epic_entity.id)
            for task in tasks:
                if not task.archived:
                    task_status = task.status_fk.name if task.status_fk else 'todo'
                    items.append({
                        'type': 'task',
                        'id': task.id,
                        'title': task.title or 'Untitled Task',
                        'status': task_status,
                        'status_display': get_status_display(task),
                        'priority': task.priority or '',
                        'project_id': project,
                        'epic_id': epic_entity.id,
                        'epic_title': epic_title
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
                        if entity.epic_id:
                            url = reverse('task_detail', kwargs={
                                'project': entity.project_id, 
                                'epic': entity.epic_id, 
                                'task': entity.id
                            })
                        else:
                            url = reverse('task_detail_no_epic', kwargs={
                                'project': entity.project_id,
                                'task': entity.id
                            })
                    elif entity.type == 'subtask':
                        if entity.epic_id:
                            url = reverse('subtask_detail', kwargs={
                                'project': entity.project_id,
                                'epic': entity.epic_id,
                                'task': entity.task_id,
                                'subtask': entity.id
                            })
                        else:
                            url = reverse('subtask_detail_no_epic', kwargs={
                                'project': entity.project_id,
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
                    
                    # Get seq_id from entity
                    seq_id = entity.seq_id or ''
                    
                    results.append({
                        'type': entity.type,
                        'title': entity.title,
                        'url': url,
                        'seq_id': seq_id,
                        'snippet': highlight_snippet(snippet, query)
                    })
        except Exception as e:
            logger.error(f"FTS5 search failed: {e}")
            # FTS5 should always work since we're using SQLite as primary storage
            # If it fails, return empty results rather than falling back to file search
    
    # Return results (empty if FTS5 failed or no query)
    return render(request, 'pm/search.html', {
        'query': query,
        'results': results,
    })


def notes_list(request):
    """Display list of all notes."""
    notes = []
    note_entities = Note.objects.all().order_by('-updated', '-created')
    
    for entity in note_entities:
        metadata, content = load_note(entity.id)
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
                'id': entity.id,
                'title': metadata.get('title', 'Untitled Note'),
                'created': metadata.get('created', ''),
                'updated': metadata.get('updated', ''),
                'people': people_with_ids,
                'labels': metadata.get('labels', []),
                'preview': content[:200] if content else ''
            })
    
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
    
    # Query all entity person links for this person
    from django.contrib.contenttypes.models import ContentType
    
    # Get content types for our models
    project_ct = ContentType.objects.get_for_model(Project)
    epic_ct = ContentType.objects.get_for_model(Epic)
    task_ct = ContentType.objects.get_for_model(Task)
    subtask_ct = ContentType.objects.get_for_model(Subtask)
    note_ct = ContentType.objects.get_for_model(Note)
    
    # Get all entity person links for this person
    try:
        person_obj = Person.objects.get(id=person_id)
    except Person.DoesNotExist:
        return references
    
    entity_links = EntityPersonLink.objects.filter(person=person_obj)
    
    for link in entity_links:
        ct_id = link.content_type_id
        obj_id = link.object_id
        
        try:
            if ct_id == project_ct.id:
                entity = Project.objects.get(id=obj_id)
                references['projects'].append({
                    'id': entity.id,
                    'title': entity.title or 'Untitled Project',
                    'url': reverse('project_detail', kwargs={'project': entity.id})
                })
            elif ct_id == epic_ct.id:
                entity = Epic.objects.get(id=obj_id)
                project_title = entity.project.title if entity.project else 'Untitled Project'
                
                references['epics'].append({
                    'id': entity.id,
                    'seq_id': entity.seq_id or '',
                    'title': entity.title or 'Untitled Epic',
                    'project_id': entity.project_id,
                    'project_title': project_title,
                    'url': reverse('epic_detail', kwargs={'project': entity.project_id, 'epic': entity.id})
                })
            elif ct_id == task_ct.id:
                entity = Task.objects.select_related('project', 'epic').get(id=obj_id)
                project_title = entity.project.title if entity.project else 'Untitled Project'
                epic_title = entity.epic.title if entity.epic else None
                
                if entity.epic_id:
                    url = reverse('task_detail', kwargs={'project': entity.project_id, 'epic': entity.epic_id, 'task': entity.id})
                else:
                    url = reverse('task_detail_no_epic', kwargs={'project': entity.project_id, 'task': entity.id})
                
                references['tasks'].append({
                    'id': entity.id,
                    'seq_id': entity.seq_id or '',
                    'title': entity.title or 'Untitled Task',
                    'project_id': entity.project_id,
                    'epic_id': entity.epic_id,
                    'epic_title': epic_title,
                    'project_title': project_title,
                    'url': url
                })
            elif ct_id == subtask_ct.id:
                entity = Subtask.objects.select_related('project', 'epic', 'task').get(id=obj_id)
                project_title = entity.project.title if entity.project else 'Untitled Project'
                epic_title = entity.epic.title if entity.epic else None
                task_title = entity.task.title if entity.task else None
                
                if entity.epic_id:
                    url = reverse('subtask_detail', kwargs={'project': entity.project_id, 'epic': entity.epic_id, 'task': entity.task_id, 'subtask': entity.id})
                else:
                    url = reverse('subtask_detail_no_epic', kwargs={'project': entity.project_id, 'task': entity.task_id, 'subtask': entity.id})
                
                references['subtasks'].append({
                    'id': entity.id,
                    'seq_id': entity.seq_id or '',
                    'title': entity.title or 'Untitled Subtask',
                    'project_id': entity.project_id,
                    'epic_id': entity.epic_id,
                    'task_id': entity.task_id,
                    'task_title': task_title,
                    'epic_title': epic_title,
                    'project_title': project_title,
                    'url': url
                })
            elif ct_id == note_ct.id:
                entity = Note.objects.get(id=obj_id)
                references['notes'].append({
                    'id': entity.id,
                    'title': entity.title or 'Untitled Note',
                    'created': entity.created or '',
                    'updated': entity.updated or '',
                    'url': reverse('note_detail', kwargs={'note_id': entity.id})
                })
        except (Project.DoesNotExist, Epic.DoesNotExist, Task.DoesNotExist, Subtask.DoesNotExist, Note.DoesNotExist):
            # Entity was deleted but link still exists - skip it
            pass
    
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
    
    # Handle POST requests for editing person details
    if request.method == 'POST':
        quick_update = request.POST.get('quick_update', '')
        
        # Handle inline field updates
        if quick_update == 'update_field':
            field_name = request.POST.get('field_name', '')
            field_value = request.POST.get('field_value', '')
            
            if field_name in ['display_name', 'email', 'phone', 'job_title', 'company']:
                person_metadata[field_name] = field_value
                save_person(person_id, person_metadata, person_content)
                
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': True})
                return redirect('person_detail', person_id=person_id)
        
        # Handle content (notes) updates
        elif quick_update == 'content':
            new_content = request.POST.get('content', '')
            person_content = new_content
            save_person(person_id, person_metadata, person_content)
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                # Render the markdown content
                from pm.templatetags.markdown_extras import markdownify
                rendered_content = markdownify(new_content) if new_content else ''
                return JsonResponse({'success': True, 'content': rendered_content})
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
        'total_refs': total_refs
    })


def load_note(note_id, metadata_only=False):
    """Load a note from database."""
    # Basic validation - ensure note_id is safe
    if not note_id or '/' in note_id or '..' in note_id:
        return None, None
    
    try:
        entity = Note.objects.select_related('status_fk').get(id=note_id)
        # Build metadata from Entity fields
        metadata = _build_metadata_from_entity(entity)
        metadata = _merge_people_from_entityperson(entity, metadata)
        metadata['status_display'] = get_status_display(entity)
        content = entity.content if not metadata_only else None
        return metadata, content
    except Entity.DoesNotExist:
        return None, None


def save_note(note_id, metadata, content):
    """Save a note to database."""
    # Basic validation - ensure note_id is safe
    if not note_id or '/' in note_id or '..' in note_id:
        raise Http404("Invalid note ID")
    
    # Ensure notes have a default "Active" status for database compatibility
    if 'status' not in metadata:
        metadata['status'] = 'active'

    # Extract updates text, people tags, labels for search
    updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
    people_tags = metadata.get('people', [])
    labels = metadata.get('labels', [])
    
    # Save to database and sync search index
    index_storage.sync_entity(
        entity_id=note_id,
        entity_type='note',
        metadata=metadata,
        content=content or '',
        updates_text=updates_text,
        people_tags=people_tags,
        labels=labels
    )


def note_detail(request, note_id):
    """Display or edit a note."""
    metadata, content = load_note(note_id)
    if metadata is None:
        raise Http404("Note not found")
    
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
                t_meta, t_content = load_task(project_id, task_id, epic_id=epic_id)
                if t_meta:
                    notes_list = t_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        t_meta['notes'] = notes_list
                        save_task(project_id, task_id, t_meta, t_content, epic_id=epic_id)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'unlink_task':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            if project_id and epic_id and task_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task'):
                t_meta, t_content = load_task(project_id, task_id, epic_id=epic_id)
                if t_meta:
                    notes_list = t_meta.get('notes', [])
                    t_meta['notes'] = [n for n in notes_list if n != note_id]
                    save_task(project_id, task_id, t_meta, t_content, epic_id=epic_id)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'link_subtask':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            subtask_id = request.POST.get('subtask_id', '').strip()
            if project_id and epic_id and task_id and subtask_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task') and validate_id(subtask_id, 'subtask'):
                s_meta, s_content = load_subtask(project_id, task_id, subtask_id, epic_id=epic_id)
                if s_meta:
                    notes_list = s_meta.get('notes', [])
                    if note_id not in notes_list:
                        notes_list.append(note_id)
                        s_meta['notes'] = notes_list
                        save_subtask(project_id, task_id, subtask_id, s_meta, s_content, epic_id=epic_id)
            return redirect('note_detail', note_id=note_id)
        
        elif quick_update == 'unlink_subtask':
            project_id = request.POST.get('project_id', '').strip()
            epic_id = request.POST.get('epic_id', '').strip()
            task_id = request.POST.get('task_id', '').strip()
            subtask_id = request.POST.get('subtask_id', '').strip()
            if project_id and epic_id and task_id and subtask_id and is_valid_project_id(project_id) and validate_id(epic_id, 'epic') and validate_id(task_id, 'task') and validate_id(subtask_id, 'subtask'):
                s_meta, s_content = load_subtask(project_id, task_id, subtask_id, epic_id=epic_id)
                if s_meta:
                    notes_list = s_meta.get('notes', [])
                    s_meta['notes'] = [n for n in notes_list if n != note_id]
                    save_subtask(project_id, task_id, subtask_id, s_meta, s_content, epic_id=epic_id)
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
                # Ensure person exists (create if needed)
                person_normalized = ensure_person_exists(person)
                people_list = normalize_people(metadata.get('people', []))
                if person_normalized not in people_list:
                    people_list.append(person_normalized)
                    metadata['people'] = people_list
                    save_note(note_id, metadata, content)
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
                    
                    # Return JSON for AJAX requests
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        # Fetch epics and tasks for this project
                        epics_data = []
                        tasks_data = []
                        
                        epic_entities = Epic.objects.filter(project_id=project_id)
                        for epic_entity in epic_entities:
                            epic_meta = _build_metadata_from_entity(epic_entity)
                            epics_data.append({
                                'id': epic_entity.id,
                                'title': epic_entity.title or 'Untitled Epic',
                                'seq_id': epic_entity.seq_id or ''
                            })
                        
                        task_entities = Task.objects.filter(project_id=project_id)
                        for task_entity in task_entities:
                            task_meta = _build_metadata_from_entity(task_entity)
                            epic_title = 'No Epic'
                            if task_entity.epic_id:
                                try:
                                    epic = Epic.objects.get(id=task_entity.epic_id)
                                    epic_title = epic.title or 'Untitled Epic'
                                except Entity.DoesNotExist:
                                    pass
                            
                            tasks_data.append({
                                'id': task_entity.id,
                                'title': task_entity.title or 'Untitled Task',
                                'seq_id': task_entity.seq_id or '',
                                'epic_id': task_entity.epic_id or None,
                                'epic_title': epic_title
                            })
                        
                        return JsonResponse({
                            'success': True,
                            'project_id': project_id,
                            'project_title': p_meta.get('title', 'Untitled Project'),
                            'epics': epics_data,
                            'tasks': tasks_data
                        })
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
            # Create a new task (with or without epic)
            title = request.POST.get('title', 'New Task').strip()
            epic_id = request.POST.get('epic_id', '').strip() or None
            project_id = metadata.get('note_project_id', '').strip()
            
            if title and project_id and is_valid_project_id(project_id):
                # If epic_id provided, validate it
                if epic_id and not validate_id(epic_id, 'epic'):
                    epic_id = None
                
                # If epic_id provided, verify epic exists and belongs to project
                if epic_id:
                    e_meta, _ = load_epic(project_id, epic_id, metadata_only=True)
                    if not e_meta:
                        epic_id = None
                
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
                save_task(project_id, task_id, task_metadata, '', epic_id=epic_id)
                # Track tasks created in this note
                note_tasks = metadata.get('note_tasks', [])
                if task_id not in note_tasks:
                    note_tasks.append(task_id)
                metadata['note_tasks'] = note_tasks
                save_note(note_id, metadata, content)
            return redirect('note_detail', note_id=note_id)
        elif quick_update == 'content':
            # Handle inline content editing with AJAX
            new_content = request.POST.get('content', '').strip()
            content = new_content
            save_note(note_id, metadata, content)
            
            # Return JSON response for AJAX request
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                rendered_content = render_markdown(content) if content else ''
                return JsonResponse({
                    'success': True,
                    'content': rendered_content
                })
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
        epics = Epic.objects.filter(project_id=note_project_id)
        for epic in epics:
            project_epics.append({
                'id': epic.id,
                'title': epic.title or 'Untitled Epic',
                'seq_id': epic.seq_id or ''
            })
        project_epics.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))
    
    # Get tasks created in this note
    note_task_ids = metadata.get('note_tasks', [])
    note_tasks = []
    if note_project_id:
        for task_id in note_task_ids:
            if validate_id(task_id, 'task'):
                try:
                    task = Task.objects.get(id=task_id, project_id=note_project_id)
                    # Find epic title
                    epic_title = 'Untitled Epic'
                    if task.epic_id:
                        try:
                            epic = Epic.objects.get(id=task.epic_id)
                            epic_title = epic.title or 'Untitled Epic'
                        except Epic.DoesNotExist:
                            pass
                    
                    note_tasks.append({
                        'id': task_id,
                        'epic_id': task.epic_id,
                        'epic_title': epic_title,
                        'title': task.title or 'Untitled Task',
                        'seq_id': task.seq_id or ''
                    })
                except Entity.DoesNotExist:
                    pass
                except Entity.DoesNotExist:
                    pass
    
    # Get all tasks from the note project (for linking in the creation section)
    all_tasks_available = []
    if note_project_id:
        tasks = Task.objects.filter(project_id=note_project_id).exclude(id__in=note_task_ids)
        for task in tasks:
            # Find epic title
            epic_title = 'Untitled Epic'
            if task.epic_id:
                try:
                    epic = Epic.objects.get(id=task.epic_id)
                    epic_title = epic.title or 'Untitled Epic'
                except Epic.DoesNotExist:
                    pass
            
            all_tasks_available.append({
                'id': task.id,
                'epic_id': task.epic_id,
                'epic_title': epic_title,
                'title': task.title or 'Untitled Task',
                'seq_id': task.seq_id or ''
            })
        all_tasks_available.sort(key=lambda x: (x.get('seq_id', ''), x.get('title', '')))

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
        'backlinks': backlinks,
        'available_projects': available_projects,
        'available_epics': available_epics,
        'available_tasks': available_tasks,
        'available_subtasks': available_subtasks,
        'note_project': note_project,
        'note_epics': note_epics,
        'project_epics': project_epics,
        'note_tasks': note_tasks,
        'all_tasks_available': all_tasks_available
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
            # Ensure all people exist (create if needed)
            metadata['people'] = ensure_people_exist(people)
        
        save_note(note_id, metadata, content)
        return redirect('note_detail', note_id=note_id)
    
    return render(request, 'pm/new_note.html')


def delete_note(request, note_id):
    """Delete a note."""
    if request.method == 'POST':
        # Basic validation
        if not note_id or '/' in note_id or '..' in note_id:
            raise Http404("Invalid note ID")
        # Delete note from database
        try:
            note = Note.objects.get(id=note_id)
            note.delete()
        except Entity.DoesNotExist:
            pass
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
    project_entities = Project.objects.select_related('status_fk').exclude(id=INBOX_PROJECT_ID)
    for project in project_entities:
        if not project.archived:
            projects.append({
                'id': project.id,
                'title': project.title or 'Untitled Project'
            })
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
            # Ensure all people exist (create if needed)
            metadata['people'] = ensure_people_exist(people)
        
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
        
        # Epic is optional - check if provided, otherwise create task directly under project
        epic_id = request.POST.get('epic_id', '').strip() or None
        
        # If epic_id provided, validate it
        if epic_id:
            if not validate_id(epic_id, 'epic'):
                epic_id = None
            else:
                # Verify epic exists
                epic_meta, _ = load_epic(project_id, epic_id, metadata_only=True)
                if not epic_meta:
                    epic_id = None
        
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
        save_task(project_id, task_id, task_metadata, content, epic_id=epic_id)
        
        if epic_id:
            url = reverse('task_detail', kwargs={'project': project_id, 'epic': epic_id, 'task': task_id})
        else:
            url = reverse('task_detail_no_epic', kwargs={'project': project_id, 'task': task_id})
        
        return JsonResponse({
            'success': True,
            'type': 'task',
            'id': task_id,
            'title': title,
            'url': url
        })
    
    return JsonResponse({'success': False, 'error': 'Invalid item_type'}, status=400)


def search_persons(request):
    """API endpoint to search persons by name."""
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'GET required'}, status=400)
    
    query = request.GET.get('q', '').strip().lower()
    if not query:
        return JsonResponse({'success': True, 'persons': []})
    
    # Search persons by name (case-insensitive, partial match)
    persons = Person.objects.filter(name__icontains=query).order_by('name')[:10]
    
    results = []
    for person in persons:
        results.append({
            'id': person.id,
            'name': person.name,
            'display_name': person.display_name or person.name
        })
    
    return JsonResponse({'success': True, 'persons': results})


def extract_mentions(content):
    """Extract @mentions from content text.
    
    Finds patterns like @username or @username with spaces.
    Returns a list of unique normalized person names (without @ prefix).
    """
    if not content:
        return []
    
    # Regex pattern to match @mentions
    # Matches @ followed by word characters, spaces, hyphens, underscores
    # Stops at punctuation or whitespace boundaries
    pattern = r'@([\w\s\-_]+?)(?=\s|$|[^\w\s\-_])'
    
    matches = re.findall(pattern, content)
    
    # Normalize mentions: strip whitespace, remove empty
    mentions = []
    seen = set()
    for match in matches:
        normalized = match.strip()
        if normalized and normalized.lower() not in seen:
            mentions.append(normalized)
            seen.add(normalized.lower())
    
    return mentions


def move_task(request, project, epic, task):
    """Move a task to another project/epic."""
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
            epic_entities = Epic.objects.filter(project_id=p_id)
            for epic in epic_entities:
                if not epic.archived:
                    epics.append({
                        'id': epic.id,
                        'title': epic.title or 'Untitled Epic',
                        'seq_id': epic.seq_id or ''
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
    task_metadata, task_content = load_task(project, task, epic_id=epic)
    if not task_metadata:
        return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)
    
    # Get target project and epic (epic is optional)
    target_project = request.POST.get('target_project', '').strip()
    target_epic = request.POST.get('target_epic', '').strip() or None
    
    if not target_project:
        return JsonResponse({'success': False, 'error': 'Target project required'}, status=400)
    
    if not is_valid_project_id(target_project):
        return JsonResponse({'success': False, 'error': 'Invalid target project'}, status=400)
    
    # If epic provided, validate it
    if target_epic:
        if not validate_id(target_epic, 'epic'):
            return JsonResponse({'success': False, 'error': 'Invalid target epic'}, status=400)
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
        if target_epic:
            task_metadata['epic_id'] = target_epic
        else:
            task_metadata.pop('epic_id', None)
        
        # Add move activity
        old_location = f"{project}/{epic if epic else 'direct'}"
        new_location = f"{target_project}/{target_epic if target_epic else 'direct'}"
        add_activity_entry(task_metadata, 'moved', old_location, new_location)
        
        # Load all subtasks first
        subtasks_to_move = []
        subtask_entities = Subtask.objects.filter(project_id=project, task_id=task)
        if epic:
            subtask_entities = subtask_entities.filter(epic_id=epic)
        else:
            subtask_entities = subtask_entities.filter(epic_id__isnull=True)
        
        for subtask in subtask_entities:
            subtask_meta = _build_metadata_from_entity(subtask)
            subtasks_to_move.append((subtask.id, subtask_meta, subtask.content))
        
        # Save task to new location
        save_task(target_project, task, task_metadata, task_content, epic_id=target_epic)
        
        # Move all subtasks
        for subtask_id, subtask_meta, subtask_content in subtasks_to_move:
            # Generate new seq_id for subtask in new project
            new_subtask_seq_id = get_next_seq_id(target_project, 'subtask')
            subtask_meta['seq_id'] = new_subtask_seq_id
            subtask_meta['project_id'] = target_project
            if target_epic:
                subtask_meta['epic_id'] = target_epic
            else:
                subtask_meta.pop('epic_id', None)
            subtask_meta['task_id'] = task
            save_subtask(target_project, task, subtask_id, subtask_meta, subtask_content, epic_id=target_epic)
        
        # Update dependencies: tasks that reference this task need to be updated
        # Find all tasks/subtasks that have this task in their blocks/blocked_by
        all_tasks = Task.objects.all()
        for t_entity in all_tasks:
            t_meta = _build_metadata_from_entity(t_entity)
            t_content = t_entity.content
            
            updated = False
            # Check blocks (stored in dependencies array)
            dependencies = t_meta.get('dependencies', [])
            if task in dependencies:
                add_activity_entry(t_meta, 'dependency_updated', None, f"blocks {task_metadata.get('title', task)}")
                updated = True
            # Check blocked_by (also in dependencies)
            if task in dependencies:
                add_activity_entry(t_meta, 'dependency_updated', None, f"blocked by {task_metadata.get('title', task)}")
                updated = True
            
            if updated:
                save_task(t_entity.project_id, t_entity.id, t_meta, t_content, epic_id=t_entity.epic_id)
        
        # Check subtasks for dependencies
        all_subtasks = Subtask.objects.all()
        for s_entity in all_subtasks:
            s_meta = _build_metadata_from_entity(s_entity)
            s_content = s_entity.content
            
            s_updated = False
            dependencies = s_meta.get('dependencies', [])
            if task in dependencies:
                add_activity_entry(s_meta, 'dependency_updated', None, f"blocks {task_metadata.get('title', task)}")
                s_updated = True
            if task in dependencies:
                add_activity_entry(s_meta, 'dependency_updated', None, f"blocked by {task_metadata.get('title', task)}")
                s_updated = True
            
            if s_updated:
                save_subtask(s_entity.project_id, s_entity.task_id, s_entity.id, s_meta, s_content, epic_id=s_entity.epic_id)
        
        # Update stats for both projects
        update_project_stats(project)
        update_project_stats(target_project)
        
        if target_epic:
            url = reverse('task_detail', kwargs={'project': target_project, 'epic': target_epic, 'task': task})
        else:
            url = reverse('task_detail_no_epic', kwargs={'project': target_project, 'task': task})
        
        return JsonResponse({
            'success': True,
            'url': url
        })
    
    except Exception as e:
        logger.error(f"Error moving task: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def move_task_no_epic(request, project, task):
    """Move a task without epic to another project/epic."""
    return move_task(request, project, None, task)


def move_epic(request, project, epic):
    """Move an epic to another project, moving all tasks and subtasks."""
    if request.method == 'GET':
        # Return projects list (epics don't need epic selection, just project)
        projects = get_all_projects_for_dropdown()
        # Include inbox in the list for completeness
        projects.insert(0, {'id': INBOX_PROJECT_ID, 'title': 'Inbox'})
        
        return JsonResponse({
            'projects': projects
        })
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
    
    # Validate current epic exists
    epic_metadata, epic_content = load_epic(project, epic)
    if not epic_metadata:
        return JsonResponse({'success': False, 'error': 'Epic not found'}, status=404)
    
    # Get target project
    target_project = request.POST.get('target_project', '').strip()
    
    if not target_project:
        return JsonResponse({'success': False, 'error': 'Target project required'}, status=400)
    
    if not is_valid_project_id(target_project):
        return JsonResponse({'success': False, 'error': 'Invalid target project'}, status=400)
    
    # Do not allow moving to same location
    if project == target_project:
        return JsonResponse({'success': False, 'error': 'Epic is already in this project'}, status=400)
    
    try:
        # Generate new seq_id for target project
        new_seq_id = get_next_seq_id(target_project, 'epic')
        epic_metadata['seq_id'] = new_seq_id
        
        # Update project reference in metadata
        epic_metadata['project_id'] = target_project
        
        # Add move activity
        old_location = f"{project}"
        new_location = f"{target_project}"
        add_activity_entry(epic_metadata, 'moved', old_location, new_location)
        
        # Load all tasks and their subtasks
        tasks_to_move = []
        task_entities = Task.objects.filter(project_id=project, epic_id=epic)
        for task_entity in task_entities:
            task_meta = _build_metadata_from_entity(task_entity)
            task_content = task_entity.content
            
            # Load subtasks for this task
            subtasks_to_move = []
            subtask_entities = Subtask.objects.filter(project_id=project, task_id=task_entity.id, epic_id=epic)
            for subtask in subtask_entities:
                subtask_meta = _build_metadata_from_entity(subtask)
                subtasks_to_move.append((subtask.id, subtask_meta, subtask.content))
            
            tasks_to_move.append((task_entity.id, task_meta, task_content, subtasks_to_move))
        
        # Save epic to new location
        save_epic(target_project, epic, epic_metadata, epic_content)
        
        # Move all tasks and their subtasks
        for task_id, task_meta, task_content, subtasks_to_move in tasks_to_move:
            # Generate new seq_id for task in new project
            new_task_seq_id = get_next_seq_id(target_project, 'task')
            task_meta['seq_id'] = new_task_seq_id
            task_meta['project_id'] = target_project
            task_meta['epic_id'] = epic
            
            # Add move activity for task
            add_activity_entry(task_meta, 'moved', f"{project}/{epic}", f"{target_project}/{epic}")
            
            # Save task to new location
            save_task(target_project, task_id, task_meta, task_content, epic_id=epic)
            
            # Move all subtasks
            for subtask_id, subtask_meta, subtask_content in subtasks_to_move:
                # Generate new seq_id for subtask in new project
                new_subtask_seq_id = get_next_seq_id(target_project, 'subtask')
                subtask_meta['seq_id'] = new_subtask_seq_id
                subtask_meta['project_id'] = target_project
                subtask_meta['epic_id'] = epic
                subtask_meta['task_id'] = task_id
                
                # Add move activity for subtask
                add_activity_entry(subtask_meta, 'moved', f"{project}/{epic}/{task_id}", f"{target_project}/{epic}/{task_id}")
                
                save_subtask(target_project, task_id, subtask_id, subtask_meta, subtask_content, epic_id=epic)
        
        # Update dependencies: entities that reference tasks in this epic need to be updated
        # Find all tasks/subtasks that have any task from this epic in their blocks/blocked_by
        task_ids_moved = [t[0] for t in tasks_to_move]
        subtask_ids_moved = []
        for _, _, _, subtasks in tasks_to_move:
            subtask_ids_moved.extend([s[0] for s in subtasks])
        
        # Query all tasks for dependencies
        all_tasks = Task.objects.all()
        for t_entity in all_tasks:
            t_meta = _build_metadata_from_entity(t_entity)
            t_content = t_entity.content
            
            updated = False
            # Check blocks for moved tasks (blocks/blocked_by are in dependencies or metadata)
            for moved_task_id in task_ids_moved:
                # Find the task metadata for title
                moved_task_title = moved_task_id
                for tid, tmeta, _, _ in tasks_to_move:
                    if tid == moved_task_id:
                        moved_task_title = tmeta.get('title', moved_task_id)
                        break
                # Check if moved_task_id is in dependencies or blocks/blocked_by
                dependencies = t_meta.get('dependencies', [])
                blocks = t_meta.get('blocks', [])
                blocked_by = t_meta.get('blocked_by', [])
                if moved_task_id in blocks or moved_task_id in dependencies:
                    add_activity_entry(t_meta, 'dependency_updated', None, f"blocks {moved_task_title}")
                    updated = True
                if moved_task_id in blocked_by or moved_task_id in dependencies:
                    add_activity_entry(t_meta, 'dependency_updated', None, f"blocked by {moved_task_title}")
                    updated = True
            
            if updated:
                save_task(t_entity.project_id, t_entity.id, t_meta, t_content, epic_id=t_entity.epic_id)
        
        # Query all subtasks for dependencies
        all_subtasks = Subtask.objects.all()
        for s_entity in all_subtasks:
            s_meta = _build_metadata_from_entity(s_entity)
            s_content = s_entity.content
            
            s_updated = False
            for moved_task_id in task_ids_moved:
                # Find the task metadata for title
                moved_task_title = moved_task_id
                for tid, tmeta, _, _ in tasks_to_move:
                    if tid == moved_task_id:
                        moved_task_title = tmeta.get('title', moved_task_id)
                        break
                dependencies = s_meta.get('dependencies', [])
                blocks = s_meta.get('blocks', [])
                blocked_by = s_meta.get('blocked_by', [])
                if moved_task_id in blocks or moved_task_id in dependencies:
                    add_activity_entry(s_meta, 'dependency_updated', None, f"blocks {moved_task_title}")
                    s_updated = True
                if moved_task_id in blocked_by or moved_task_id in dependencies:
                    add_activity_entry(s_meta, 'dependency_updated', None, f"blocked by {moved_task_title}")
                    s_updated = True
            
            if s_updated:
                save_subtask(s_entity.project_id, s_entity.task_id, s_entity.id, s_meta, s_content, epic_id=s_entity.epic_id)
        
        # Update stats for both projects
        update_project_stats(project)
        update_project_stats(target_project)
        
        url = reverse('epic_detail', kwargs={'project': target_project, 'epic': epic})
        
        return JsonResponse({
            'success': True,
            'url': url
        })
    
    except Exception as e:
        logger.error(f"Error moving epic: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def move_subtask(request, project, task, subtask, epic=None):
    """Move a subtask to another task/project."""
    if request.method == 'GET':
        # Return projects, epics, and tasks for selection
        projects = get_all_projects_for_dropdown()
        # Include inbox in the list for completeness
        projects.insert(0, {'id': INBOX_PROJECT_ID, 'title': 'Inbox'})
        
        # Get epics and tasks for each project
        projects_with_data = []
        for p in projects:
            p_id = p['id']
            epics = []
            epic_entities = Epic.objects.filter(project_id=p_id)
            for epic in epic_entities:
                if not epic.archived:
                    # Get tasks for this epic
                    tasks = []
                    task_entities = Task.objects.filter(project_id=p_id, epic_id=epic.id)
                    for task in task_entities:
                        tasks.append({
                            'id': task.id,
                            'title': task.title or 'Untitled Task',
                            'seq_id': task.seq_id or ''
                        })
                    
                    epics.append({
                        'id': epic.id,
                        'title': epic.title or 'Untitled Epic',
                        'seq_id': epic.seq_id or '',
                        'tasks': sorted(tasks, key=lambda t: (t.get('seq_id', ''), t.get('title', '')))
                    })
            
            # Also get direct tasks (without epic)
            direct_tasks = []
            direct_task_entities = Task.objects.filter(project_id=p_id, epic__isnull=True)
            for task in direct_task_entities:
                direct_tasks.append({
                    'id': task.id,
                    'title': task.title or 'Untitled Task',
                    'seq_id': task.seq_id or ''
                })
            
            projects_with_data.append({
                'id': p_id,
                'title': p['title'],
                'epics': sorted(epics, key=lambda e: (e.get('seq_id', ''), e.get('title', ''))),
                'direct_tasks': sorted(direct_tasks, key=lambda t: (t.get('seq_id', ''), t.get('title', '')))
            })
        
        return JsonResponse({
            'projects': projects_with_data
        })
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
    
    # Validate current subtask exists
    subtask_metadata, subtask_content = load_subtask(project, task, subtask, epic_id=epic)
    if not subtask_metadata:
        return JsonResponse({'success': False, 'error': 'Subtask not found'}, status=404)
    
    # Get target project, epic (optional), and task
    target_project = request.POST.get('target_project', '').strip()
    target_epic = request.POST.get('target_epic', '').strip() or None
    target_task = request.POST.get('target_task', '').strip()
    
    if not target_project:
        return JsonResponse({'success': False, 'error': 'Target project required'}, status=400)
    
    if not is_valid_project_id(target_project):
        return JsonResponse({'success': False, 'error': 'Invalid target project'}, status=400)
    
    if not target_task:
        return JsonResponse({'success': False, 'error': 'Target task required'}, status=400)
    
    if not validate_id(target_task, 'task'):
        return JsonResponse({'success': False, 'error': 'Invalid target task'}, status=400)
    
    # If epic provided, validate it
    if target_epic:
        if not validate_id(target_epic, 'epic'):
            return JsonResponse({'success': False, 'error': 'Invalid target epic'}, status=400)
        # Verify target epic exists
        target_epic_meta, _ = load_epic(target_project, target_epic, metadata_only=True)
        if not target_epic_meta:
            return JsonResponse({'success': False, 'error': 'Target epic not found'}, status=404)
    
    # Verify target task exists
    target_task_meta, _ = load_task(target_project, target_task, epic_id=target_epic, metadata_only=True)
    if not target_task_meta:
        return JsonResponse({'success': False, 'error': 'Target task not found'}, status=404)
    
    # Do not allow moving to same location
    if project == target_project and epic == target_epic and task == target_task:
        return JsonResponse({'success': False, 'error': 'Subtask is already in this location'}, status=400)
    
    try:
        # Generate new seq_id for subtask in target project
        new_seq_id = get_next_seq_id(target_project, 'subtask')
        subtask_metadata['seq_id'] = new_seq_id
        
        # Update project/epic/task references in metadata
        subtask_metadata['project_id'] = target_project
        if target_epic:
            subtask_metadata['epic_id'] = target_epic
        else:
            subtask_metadata.pop('epic_id', None)
        subtask_metadata['task_id'] = target_task
        
        # Add move activity
        old_location = f"{project}/{epic if epic else 'direct'}/{task}"
        new_location = f"{target_project}/{target_epic if target_epic else 'direct'}/{target_task}"
        add_activity_entry(subtask_metadata, 'moved', old_location, new_location)
        
        # Save subtask to new location
        save_subtask(target_project, target_task, subtask, subtask_metadata, subtask_content, epic_id=target_epic)
        
        # Update dependencies: entities that reference this subtask need to be updated
        # Query all tasks for dependencies
        all_tasks = Task.objects.all()
        for t_entity in all_tasks:
            t_meta = _build_metadata_from_entity(t_entity)
            t_content = t_entity.content
            
            updated = False
            # Check blocks
            if subtask in t_meta.get('blocks', []):
                add_activity_entry(t_meta, 'dependency_updated', None, f"blocks {subtask_metadata.get('title', subtask)}")
                updated = True
            # Check blocked_by
            if subtask in t_meta.get('blocked_by', []):
                add_activity_entry(t_meta, 'dependency_updated', None, f"blocked by {subtask_metadata.get('title', subtask)}")
                updated = True
            
            if updated:
                save_task(t_entity.project_id, t_entity.id, t_meta, t_content, epic_id=t_entity.epic_id)
        
        # Query all subtasks for dependencies
        all_subtasks = Subtask.objects.all()
        for s_entity in all_subtasks:
            s_meta = _build_metadata_from_entity(s_entity)
            s_content = s_entity.content
            
            s_updated = False
            if subtask in s_meta.get('blocks', []):
                add_activity_entry(s_meta, 'dependency_updated', None, f"blocks {subtask_metadata.get('title', subtask)}")
                s_updated = True
            if subtask in s_meta.get('blocked_by', []):
                add_activity_entry(s_meta, 'dependency_updated', None, f"blocked by {subtask_metadata.get('title', subtask)}")
                s_updated = True
            
            if s_updated:
                save_subtask(s_entity.project_id, s_entity.task_id, s_entity.id, s_meta, s_content, epic_id=s_entity.epic_id)
        
        # Update stats for both projects
        update_project_stats(project)
        update_project_stats(target_project)
        
        if target_epic:
            url = reverse('subtask_detail', kwargs={'project': target_project, 'epic': target_epic, 'task': target_task, 'subtask': subtask})
        else:
            url = reverse('subtask_detail_no_epic', kwargs={'project': target_project, 'task': target_task, 'subtask': subtask})
        
        return JsonResponse({
            'success': True,
            'url': url
        })
    
    except Exception as e:
        logger.error(f"Error moving subtask: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def move_subtask_no_epic(request, project, task, subtask):
    """Move a subtask without epic to another task/project."""
    return move_subtask(request, project, task, subtask, epic=None)


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
        # Path will be: /uploads/YYYY/MM/filename.ext
        # Using /uploads/ instead of /static/uploads/ to avoid conflict with Django's staticfiles
        relative_path = f'uploads/{now.strftime("%Y")}/{now.strftime("%m")}/{filename}'
        
        return JsonResponse({
            'success': True,
            'url': f'/{relative_path}',
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
