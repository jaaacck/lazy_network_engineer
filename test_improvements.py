#!/usr/bin/env python
"""
Quick test script to verify the improvements work correctly.
Run this with: ./venv/bin/python test_improvements.py
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.utils import validate_id, safe_join_path
from django.http import Http404

def test_id_validation():
    """Test that ID validation works correctly."""
    print("Testing ID validation...")

    # Valid IDs
    assert validate_id('project-12345678', 'project') == True
    assert validate_id('epic-abcdef12', 'epic') == True
    assert validate_id('task-99887766', 'task') == True
    assert validate_id('subtask-aabbccdd', 'subtask') == True

    # Invalid IDs (path traversal attempts)
    assert validate_id('../../../etc/passwd', 'project') == False
    assert validate_id('project-12345678/../../secret', 'project') == False
    assert validate_id('project-GGGGGGGG', 'project') == False  # Invalid chars
    assert validate_id('project-123', 'project') == False  # Too short
    assert validate_id('epic-12345678', 'project') == False  # Wrong prefix

    print("✓ ID validation tests passed!")

def test_safe_path_joining():
    """Test that path traversal protection works."""
    print("\nTesting path traversal protection...")

    # Valid paths
    try:
        path = safe_join_path('projects', 'project-12345678.md')
        assert 'projects' in path
        print(f"✓ Valid path accepted: {path}")
    except Http404:
        print("✗ Valid path rejected!")
        return False

    # Invalid paths (should raise Http404)
    try:
        path = safe_join_path('..', '..', 'etc', 'passwd')
        print(f"✗ Path traversal not blocked: {path}")
        return False
    except Http404:
        print("✓ Path traversal blocked!")

    print("✓ Path protection tests passed!")

def test_bleach_import():
    """Test that bleach is installed and importable."""
    print("\nTesting bleach installation...")
    try:
        import bleach
        from pm.templatetags.markdown_extras import markdownify
        print("✓ Bleach installed and markdown filter works!")
    except ImportError as e:
        print(f"✗ Bleach import failed: {e}")
        return False

def test_markdown_sanitization():
    """Test that XSS protection works in markdown."""
    print("\nTesting markdown sanitization...")
    from pm.templatetags.markdown_extras import markdownify

    # Test malicious script injection
    malicious = '<script>alert("XSS")</script>Hello'
    result = markdownify(malicious)

    if '<script>' in result:
        print("✗ XSS vulnerability: script tags not sanitized!")
        return False
    else:
        print("✓ XSS protection: script tags removed")

    # Test safe markdown
    safe = '**Bold text** and *italic*'
    result = markdownify(safe)
    if '<strong>' in result and '<em>' in result:
        print("✓ Safe markdown rendered correctly")
    else:
        print("✗ Markdown rendering broken")
        return False

def main():
    print("=" * 60)
    print("Testing Django Project Manager Improvements")
    print("=" * 60)

    try:
        test_id_validation()
        test_safe_path_joining()
        test_bleach_import()
        test_markdown_sanitization()

        print("\n" + "=" * 60)
        print("✓ All tests passed! Security improvements working correctly.")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
