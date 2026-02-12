"""
Management command to rebuild the search index from database entities.
"""
from django.core.management.base import BaseCommand
from django.db import connection
from pm.models import Project, Epic, Task, Subtask, Note
from pm.storage.index_storage import IndexStorage


class Command(BaseCommand):
    help = 'Rebuild search index from database entities'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear the search index before rebuilding',
        )

    def handle(self, *args, **options):
        index_storage = IndexStorage()
        
        # Clear existing index if requested
        if options.get('clear', False):
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM search_index")
            self.stdout.write('Cleared existing search index')
        
        self.stdout.write('Starting search index rebuild from database...')
        
        total_synced = 0
        
        # Sync projects
        projects = Project.objects.all()
        for project in projects:
            try:
                # Build metadata from entity fields
                from pm.views import _build_metadata_from_entity
                metadata = _build_metadata_from_entity(project)
                
                # Extract updates text for search
                updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
                people_tags = metadata.get('people', [])
                labels = metadata.get('labels', [])
                
                # Sync to search index
                index_storage._update_search_index(
                    project.id,
                    'project',
                    project.title or '',
                    project.content or '',
                    updates_text,
                    people_tags,
                    labels
                )
                total_synced += 1
                self.stdout.write(f'  Synced project: {project.id} - {project.title}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error syncing project {project.id}: {e}'))
        
        # Sync epics
        epics = Epic.objects.all()
        for epic in epics:
            try:
                from pm.views import _build_metadata_from_entity
                metadata = _build_metadata_from_entity(epic)
                
                updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
                people_tags = metadata.get('people', [])
                labels = metadata.get('labels', [])
                
                index_storage._update_search_index(
                    epic.id,
                    'epic',
                    epic.title or '',
                    epic.content or '',
                    updates_text,
                    people_tags,
                    labels
                )
                total_synced += 1
                self.stdout.write(f'  Synced epic: {epic.id} - {epic.title}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error syncing epic {epic.id}: {e}'))
        
        # Sync tasks
        tasks = Task.objects.all()
        for task in tasks:
            try:
                from pm.views import _build_metadata_from_entity
                metadata = _build_metadata_from_entity(task)
                
                updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
                people_tags = metadata.get('people', [])
                labels = metadata.get('labels', [])
                
                index_storage._update_search_index(
                    task.id,
                    'task',
                    task.title or '',
                    task.content or '',
                    updates_text,
                    people_tags,
                    labels
                )
                total_synced += 1
                self.stdout.write(f'  Synced task: {task.id} - {task.title}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error syncing task {task.id}: {e}'))
        
        # Sync subtasks
        subtasks = Subtask.objects.all()
        for subtask in subtasks:
            try:
                from pm.views import _build_metadata_from_entity
                metadata = _build_metadata_from_entity(subtask)
                
                updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
                people_tags = metadata.get('people', [])
                labels = metadata.get('labels', [])
                
                index_storage._update_search_index(
                    subtask.id,
                    'subtask',
                    subtask.title or '',
                    subtask.content or '',
                    updates_text,
                    people_tags,
                    labels
                )
                total_synced += 1
                self.stdout.write(f'  Synced subtask: {subtask.id} - {subtask.title}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error syncing subtask {subtask.id}: {e}'))
        
        # Sync notes
        notes = Note.objects.all()
        for note in notes:
            try:
                from pm.views import _build_metadata_from_entity
                metadata = _build_metadata_from_entity(note)
                
                updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
                people_tags = metadata.get('people', [])
                labels = metadata.get('labels', [])
                
                index_storage._update_search_index(
                    note.id,
                    'note',
                    note.title or '',
                    note.content or '',
                    updates_text,
                    people_tags,
                    labels
                )
                total_synced += 1
                self.stdout.write(f'  Synced note: {note.id} - {note.title}')
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error syncing note {note.id}: {e}'))
        
        self.stdout.write(self.style.SUCCESS(f'Search index rebuild complete! Synced {total_synced} entities.'))