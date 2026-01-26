import sys
import django
from django import template

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
    """Return application version."""
    from pm.version import __version__
    return __version__
