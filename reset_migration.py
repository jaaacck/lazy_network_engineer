import django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project_manager.settings')
django.setup()

from pm.models import Project, Epic, Task, Subtask, Note, EntityPersonLink, EntityLabelLink
from django.db import connection

# Clear the tables
EntityPersonLink.objects.all().delete()
EntityLabelLink.objects.all().delete()
Subtask.objects.all().delete()
Task.objects.all().delete()
Epic.objects.all().delete()
Project.objects.all().delete()
Note.objects.all().delete()

# Reset migration state
with connection.cursor() as cursor:
    cursor.execute("DELETE FROM django_migrations WHERE app='pm' AND name='0014_migrate_entity_data'")

print('✓ Tables cleared and migration state reset')
