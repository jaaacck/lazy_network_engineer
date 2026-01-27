import sys
import os
import re
import django
from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def django_version():
    """Return Django version."""
    return django.get_version()


@register.simple_tag
def python_version():
    """Return Python version."""
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


@register.simple_tag
def app_version():
    """Return application version from CHANGELOG.md."""
    # Look for CHANGELOG.md in static files directories
    changelog_paths = [
        os.path.join(settings.BASE_DIR, 'pm', 'static', 'CHANGELOG.md'),
        os.path.join(settings.BASE_DIR, 'pm', 'static', 'pm', 'CHANGELOG.md'),
    ]
    
    # Also check STATICFILES_DIRS
    if hasattr(settings, 'STATICFILES_DIRS'):
        for static_dir in settings.STATICFILES_DIRS:
            changelog_paths.append(os.path.join(static_dir, 'CHANGELOG.md'))
            changelog_paths.append(os.path.join(static_dir, 'pm', 'CHANGELOG.md'))
    
    for changelog_path in changelog_paths:
        if os.path.exists(changelog_path):
            try:
                with open(changelog_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Look for version pattern: ## [1.2.8] - 2026-01-24
                    match = re.search(r'^## \[([\d.]+)\]', content, re.MULTILINE)
                    if match:
                        return match.group(1)
            except (IOError, OSError):
                continue
    
    # Fallback if changelog not found
    return 'Unknown'
