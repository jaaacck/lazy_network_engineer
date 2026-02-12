#!/usr/bin/env python
"""Test script to verify search snippets are working."""
import os
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.storage.index_storage import IndexStorage

print("Testing search snippet functionality...")
print("=" * 70)

index_storage = IndexStorage()

# Test searches
test_queries = ["fortigate", "certificate", "monitoring"]

for query in test_queries:
    print(f"\nSearching for: '{query}'")
    results = index_storage.search(query)
    print(f"Found {len(results)} results\n")
    
    for i, result in enumerate(results[:3], 1):
        entity = result['entity']
        print(f"{i}. [{entity._meta.model_name}] {entity.title}")
        
        # Show snippets
        if result.get('content_snippet'):
            print(f"   Content: {result['content_snippet'][:100]}...")
        if result.get('title_snippet'):
            print(f"   Title: {result['title_snippet'][:100]}")
        if result.get('updates_snippet'):
            print(f"   Updates: {result['updates_snippet'][:100]}")
        if result.get('people_snippet'):
            print(f"   People: {result['people_snippet']}")
        if result.get('labels_snippet'):
            print(f"   Labels: {result['labels_snippet']}")
        print()

print("=" * 70)
print("Test complete!")
