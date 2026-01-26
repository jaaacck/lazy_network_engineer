import os
import re
import yaml
import logging
from datetime import datetime
from django.conf import settings
from django.http import Http404

logger = logging.getLogger('pm')

# Simple module-level cache for entity metadata and content
_entity_cache = {}


def validate_id(entity_id, entity_type):
    """
    Validate entity ID to prevent path traversal attacks.

    Args:
        entity_id: The ID to validate
        entity_type: Type of entity (project, epic, task, subtask, person)

    Returns:
        bool: True if valid, False otherwise
    """
    if not entity_id:
        return False

    # Expected format: {type}-{8 hex chars}
    pattern = rf'^{entity_type}-[a-f0-9]{{8}}$'
    return bool(re.match(pattern, entity_id))


def safe_join_path(*parts):
    """
    Safely join path components and validate they're within DATA_ROOT.

    Args:
        *parts: Path components to join

    Returns:
        str: Validated absolute path

    Raises:
        Http404: If path would escape DATA_ROOT
    """
    path = os.path.join(settings.DATA_ROOT, *parts)
    abs_path = os.path.abspath(path)
    abs_data_root = os.path.abspath(settings.DATA_ROOT)

    if not abs_path.startswith(abs_data_root):
        logger.warning(f"Path traversal attempt detected: {path}")
        raise Http404("Invalid path")

    return abs_path


def load_entity(file_path, default_title, default_status, metadata_only=False):
    """
    Generic loader for markdown files with YAML frontmatter.
    """
    if not os.path.exists(file_path):
        return None, None

    # Check cache first
    mtime = os.path.getmtime(file_path)
    cache_key = (file_path, metadata_only)
    if cache_key in _entity_cache:
        cached_mtime, cached_data = _entity_cache[cache_key]
        if cached_mtime == mtime:
            return cached_data

    try:
        # Use a small read buffer if we only need metadata
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            if metadata_only:
                # Read enough to likely cover the frontmatter
                content = f.read(4096)
            else:
                content = f.read()
    except IOError as e:
        logger.error(f"Error reading file {file_path}: {e}")
        return None, None

    result = None
    # Handle empty files
    if not content.strip():
        result = ({'title': default_title, 'status': default_status}, '')
    else:
        # Fast check for frontmatter
        has_frontmatter = content.startswith('---\n') or content.startswith('---\r\n')
        
        if has_frontmatter:
            sep = '\n---\n' if content.startswith('---\n') else '\r\n---\r\n'
            parts = content.split(sep, 1)
            if len(parts) > 1:
                try:
                    yaml_content = parts[0][4:] if sep == '\n---\n' else parts[0][5:]
                    metadata = yaml.safe_load(yaml_content) or {}
                    
                    if metadata_only:
                        result = (metadata, None)
                    else:
                        content_body = parts[1]
                        if not metadata.get('title'):
                            metadata['title'] = default_title
                        if not metadata.get('status'):
                            metadata['status'] = default_status
                        result = (metadata, content_body)
                except yaml.YAMLError as e:
                    logger.error(f"Error parsing YAML in {file_path}: {e}")
                    if metadata_only: result = ({'title': default_title, 'status': default_status}, None)
                    else: result = ({'title': default_title, 'status': default_status}, parts[1] if len(parts) > 1 else '')

        if result is None:
            if metadata_only:
                result = ({'title': default_title, 'status': default_status}, None)
            else:
                result = ({
                    'title': default_title,
                    'status': default_status,
                    'created': datetime.now().strftime('%Y-%m-%d')
                }, content)

    # Store in cache
    _entity_cache[cache_key] = (mtime, result)
    return result


def save_entity(file_path, metadata, content):
    """
    Generic saver for markdown files with YAML frontmatter.

    Args:
        file_path: Absolute path to save the file
        metadata: Dictionary of metadata to save in frontmatter
        content: Markdown content
    """
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('---\n')
            yaml.dump(metadata, f, sort_keys=False, default_flow_style=False)
            f.write('---\n\n')
            f.write(content)
        
        # Invalidate cache
        cache_key_full = (file_path, False)
        cache_key_meta = (file_path, True)
        if cache_key_full in _entity_cache: del _entity_cache[cache_key_full]
        if cache_key_meta in _entity_cache: del _entity_cache[cache_key_meta]
        
        logger.info(f"Successfully saved entity to {file_path}")
    except IOError as e:
        logger.error(f"Error writing file {file_path}: {e}")
        raise


def calculate_markdown_progress(content):
    """
    Calculate progress based on markdown tickboxes [ ] and [x].

    Returns:
        tuple: (completed_count, total_count, percentage)
    """
    if not content:
        return 0, 0, 0

    # Match [ ] or [x] or [X]
    # We look for lines starting with optional whitespace and then the bracket
    tickboxes = re.findall(r'^\s*[-*+]?\s*\[([ xX])\]', content, re.MULTILINE)
    
    if not tickboxes:
        return 0, 0, 0

    total = len(tickboxes)
    completed = sum(1 for box in tickboxes if box.lower() == 'x')
    percentage = int((completed / total) * 100) if total > 0 else 0
    
    return completed, total, percentage


def get_project_color(project_id, existing_color=None):
    """Generate or return project color."""
    import hashlib
    PROJECT_COLORS = [
        '#ff6600', '#0066ff', '#00cc66', '#cc00ff', '#ff0066',
        '#00ffcc', '#ffcc00', '#6600ff', '#ff3300', '#00ff33',
        '#ff0099', '#0099ff', '#99ff00', '#ff9900', '#9900ff'
    ]
    if existing_color:
        return existing_color
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


def label_color(label):
    """Deterministic label color."""
    import hashlib
    LABEL_COLORS = [
        '#ff6600', '#0066ff', '#00cc66', '#cc00ff', '#ff0066',
        '#00ffcc', '#ffcc00', '#6600ff', '#ff3300', '#00ff33'
    ]
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


def calculate_checklist_progress(metadata):
    """
    Calculate progress based on checklist items in metadata.

    Returns:
        tuple: (completed_count, total_count, percentage)
    """
    checklist = metadata.get('checklist', [])
    if not checklist:
        return 0, 0, 0

    total = len(checklist)
    completed = sum(1 for item in checklist if item.get('status') == 'done')
    percentage = int((completed / total) * 100) if total > 0 else 0

    return completed, total, percentage
