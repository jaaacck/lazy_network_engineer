#!/usr/bin/env python
"""Test script to verify search functionality."""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.storage.index_storage import IndexStorage

# Create index storage
index_storage = IndexStorage()

# Test searches
test_queries = [
    "fortigate",
    "certificate",
    "rconfig",
    "dns",
    "monitoring",
    "inbox"
]

for query in test_queries:
    print(f"\n🔍 Searching for: '{query}'")
    results = index_storage.search(query)
    print(f"   Found {len(results)} results:")
    for result in results[:5]:  # Show first 5 results
        entity = result['entity']
        entity_type = entity.__class__.__name__.lower()
        print(f"   - [{entity_type}] {entity.title} (ID: {entity.id})")
    if len(results) > 5:
        print(f"   ... and {len(results) - 5} more")
