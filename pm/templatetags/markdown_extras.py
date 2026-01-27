import re
import hashlib
import time
import json
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


@register.filter()
@stringfilter
def markdownify(value):
    """
    Convert markdown to HTML and sanitize output.
    """
    # #region agent log
    md_start = time.time()
    with open('/Users/jack/Documents/project_manager/.cursor/debug.log', 'a') as f:
        f.write(json.dumps({'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'G', 'location': 'templatetags/markdown_extras.py:34', 'message': 'markdownify entry', 'data': {'content_length': len(value) if value else 0}, 'timestamp': int(time.time() * 1000)}) + '\n')
    # #endregion
    if not value:
        return ""

    cache_key = f"md:{hashlib.md5(value.encode('utf-8')).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        # #region agent log
        md_end = time.time()
        with open('/Users/jack/Documents/project_manager/.cursor/debug.log', 'a') as f:
            f.write(json.dumps({'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'G', 'location': 'templatetags/markdown_extras.py:44', 'message': 'markdownify exit (cache hit)', 'data': {'duration_ms': (md_end - md_start) * 1000}, 'timestamp': int(time.time() * 1000)}) + '\n')
        # #endregion
        return cached

    # #region agent log
    md_process_start = time.time()
    with open('/Users/jack/Documents/project_manager/.cursor/debug.log', 'a') as f:
        f.write(json.dumps({'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'G', 'location': 'templatetags/markdown_extras.py:47', 'message': 'markdownify cache miss, processing', 'data': {'content_length': len(value)}, 'timestamp': int(time.time() * 1000)}) + '\n')
    # #endregion

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
    # #region agent log
    md_end = time.time()
    with open('/Users/jack/Documents/project_manager/.cursor/debug.log', 'a') as f:
        f.write(json.dumps({'sessionId': 'debug-session', 'runId': 'run1', 'hypothesisId': 'G', 'location': 'templatetags/markdown_extras.py:70', 'message': 'markdownify exit (processed)', 'data': {'duration_ms': (md_end - md_start) * 1000, 'process_duration_ms': (md_end - md_process_start) * 1000}, 'timestamp': int(time.time() * 1000)}) + '\n')
    # #endregion
    return html


@register.filter()
@stringfilter
def urlencode(value):
    """URL encode a string."""
    return quote(value, safe='')
