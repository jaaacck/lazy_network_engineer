#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from django.test import Client

# Test that views can render
client = Client()

print("Testing my_work view...")
try:
    response = client.get('/my-work/')
    print(f'  Status code: {response.status_code}')
    if response.status_code == 200:
        print('  ✓ my_work template renders successfully')
        content = response.content.decode()
        if 'work-table' in content:
            print('  ✓ Table elements found in response')
        if 'table-sort.js' in content:
            print('  ✓ JavaScript module loaded in template')
    else:
        print(f'  ✗ Unexpected status code: {response.status_code}')
except Exception as e:
    print(f'  ✗ Error: {e}')

print("\nTesting today view...")
try:
    response = client.get('/today/')
    print(f'  Status code: {response.status_code}')
    if response.status_code == 200:
        print('  ✓ today template renders successfully')
        content = response.content.decode()
        if 'work-table' in content:
            print('  ✓ Table elements found in response')
        if 'table-sort.js' in content:
            print('  ✓ JavaScript module loaded in template')
    else:
        print(f'  ✗ Unexpected status code: {response.status_code}')
except Exception as e:
    print(f'  ✗ Error: {e}')

print("\nTest complete!")
