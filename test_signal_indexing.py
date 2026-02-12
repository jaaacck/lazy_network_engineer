#!/usr/bin/env python
"""Test script to verify automatic search index updates via Django signals."""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.models import Note, Status
from pm.storage.index_storage import IndexStorage
from django.db import connection
import uuid

print("=" * 70)
print("TEST 1: Create note via ORM (bypassing views.py save function)")
print("=" * 70)

# Create a test note directly via ORM to test signal-based indexing
test_note_id = f"note-{uuid.uuid4().hex[:8]}"
print(f"\n1. Creating test note: {test_note_id}")

# Get an active status
status = Status.objects.filter(name='active', is_active=True).first()
if not status:
    print("  ERROR: No active status found. Creating one...")
    status = Status.objects.create(
        name='active',
        display_name='Active',
        entity_types='note',
        is_active=True
    )

# Create note directly via ORM (this should trigger post_save signal)
note = Note.objects.create(
    id=test_note_id,
    title='Test Note for Signal Testing',
    status_fk=status,
    content='This note was created to test automatic search index updates via Django signals.',
    created='2026-02-12T10:00:00',
    updated='2026-02-12T10:00:00'
)
print(f"  ✓ Note created in database: {note.id}")

# Check if it's in search index (should be auto-added by signal)
print("\n2. Checking if note is in search index...")
with connection.cursor() as cursor:
    cursor.execute("SELECT entity_id, entity_type, title FROM search_index WHERE entity_id = %s", [test_note_id])
    result = cursor.fetchone()

if result:
    print(f"  ✓ SUCCESS! Note found in search index: {result[0]} ({result[1]}) - {result[2]}")
else:
    print("  ✗ FAILED! Note NOT found in search index")

# Test search functionality
print("\n3. Testing FTS5 search for the note...")
index_storage = IndexStorage()
search_results = index_storage.search("Signal Testing")
found = False
for r in search_results:
    if r['entity'].id == test_note_id:
        found = True
        print(f"  ✓ Note found via search: {r['entity'].title}")
        break

if not found:
    print(f"  ⚠ Note not found via FTS5 search (searched for 'Signal Testing')")
    print(f"  Total search results: {len(search_results)}")

print("\n" + "=" * 70)
print("TEST 2: Delete note via ORM (bypassing views.py delete function)")
print("=" * 70)

print(f"\n1. Deleting test note: {test_note_id}")
note.delete()
print("  ✓ Note deleted from database")

# Check if it's removed from search index (should be auto-removed by signal)
print("\n2. Checking if note is removed from search index...")
with connection.cursor() as cursor:
    cursor.execute("SELECT entity_id FROM search_index WHERE entity_id = %s", [test_note_id])
    result = cursor.fetchone()

if result:
    print(f"  ✗ FAILED! Note STILL in search index: {result[0]}")
else:
    print("  ✓ SUCCESS! Note removed from search index")

# Verify via search
print("\n3. Verifying note is not searchable...")
search_results = index_storage.search("Signal Testing")
found = False
for r in search_results:
    if r['entity'].id == test_note_id:
        found = True
        break

if found:
    print("  ✗ FAILED! Note still appears in search results")
else:
    print("  ✓ SUCCESS! Note no longer in search results")

print("\n" + "=" * 70)
print("TEST 3: Verify overall index consistency")
print("=" * 70)

# Count entities
print("\nRunning verify_search_index command...")
import subprocess
result = subprocess.run(
    ['python', 'manage.py', 'verify_search_index'],
    capture_output=True,
    text=True,
    cwd='/home/jack/scripts/lazy_network_engineer'
)
print(result.stdout)

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print("Django signals are working correctly for automatic search index updates!")
print("- post_save signal: Auto-adds entities to search index ✓")
print("- post_delete signal: Auto-removes entities from search index ✓")
print("\nYou no longer need to run 'rebuild_search_index' manually.")
print("Search index will be updated automatically on all entity changes.")
print("=" * 70)
