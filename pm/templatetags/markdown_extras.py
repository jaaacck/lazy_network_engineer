import re
import hashlib
from urllib.parse import quote
from django import template
from django.template.defaultfilters import stringfilter
from django.core.cache import cache
import markdown
import bleach

register = template.Library()

# Allowed HTML tags and attributes for bleach sanitization
ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'a', 'code', 'pre', 'blockquote', 'hr',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'div', 'span', 'input', 'img'
]

ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title', 'class', 'data-entity-id'],
    'table': ['class'],
    'pre': ['class'],
    'code': ['class'],
    'blockquote': ['class'],
    'div': ['class'],
    'span': ['class'],
    'input': ['type', 'checked', 'disabled'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'style']
}


@register.filter()
@stringfilter
def markdownify(value):
    """
    Convert markdown to HTML and sanitize output.
    """
    if not value:
        return ""

    cache_key = f"md:{hashlib.md5(value.encode('utf-8')).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Convert entity references to links before markdown processing
    # Pattern: #project-abc123, #epic-xyz789, #task-def456, #subtask-ghi789
    def link_entity(match):
        entity_type = match.group(1)
        entity_id = match.group(0)
        
        # Build URL based on entity type
        # Since we need project context for epics/tasks/subtasks, we'll just link to search for now
        # Or we could make these non-links and just highlight them
        url = f'/search/?q={entity_id}'
        return f'<a href="{url}" class="entity-link" data-entity-id="{entity_id}">{entity_id}</a>'
    
    # Replace entity references with HTML links
    value = re.sub(r'#(project|epic|task|subtask)-[a-f0-9]{8}', link_entity, value)
    
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


@register.filter()
@stringfilter
def urlencode(value):
    """URL encode a string."""
    return quote(value, safe='')
