#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from django.db import connection

cursor = connection.cursor()
cursor.execute("SELECT entity_id, substr(content, 1, 200) FROM search_index WHERE entity_type='journalentry'")
results = cursor.fetchall()

print(f"Found {len(results)} journal entries in search index:")
for entity_id, content in results:
    print(f"\nEntity ID: {entity_id}")
    print(f"Content preview: {content}")
    print("-" * 80)

# Also search for the specific term
print("\n\nSearching for 'CHG00197121':")
cursor.execute("SELECT entity_id, entity_type, title, content FROM search_index WHERE search_index MATCH 'CHG00197121*'")
search_results = cursor.fetchall()
print(f"Found {len(search_results)} results")
for row in search_results:
    print(f"  - {row[0]} ({row[1]}): {row[2]}")
