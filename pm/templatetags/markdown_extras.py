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


@register.filter()
@stringfilter
def urlencode(value):
    """URL encode a string."""
    return quote(value, safe='')
