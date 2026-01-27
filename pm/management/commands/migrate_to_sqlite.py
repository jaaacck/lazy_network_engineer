"""
Management command to migrate all markdown files to SQLite as primary storage.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
import os
import json
import logging
import shutil
from datetime import datetime
from django.conf import settings
from pm import utils
from pm.models import Entity
from pm.storage.index_storage import IndexStorage

logger = logging.getLogger('pm')


class Command(BaseCommand):
    help = 'Migrate all markdown files to SQLite as primary storage'

    def add_arguments(self, parser):
        parser.add_argument(
            '--backup',
            action='store_true',
            help='Create backup of data directory before migration',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without actually migrating',
        )

    def handle(self, *args, **options):
        backup = options.get('backup', False)
        dry_run = options.get('dry_run', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
        
        # Create backup if requested
        if backup and not dry_run:
            backup_dir = os.path.join(settings.DATA_ROOT, f'backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}')
            self.stdout.write(f'Creating backup to {backup_dir}...')
            try:
                shutil.copytree(settings.DATA_ROOT, backup_dir, ignore=shutil.ignore_patterns('backup_*', 'uploads'))
                self.stdout.write(self.style.SUCCESS(f'Backup created: {backup_dir}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'Error creating backup: {e}'))
                return
        
        self.stdout.write('Starting migration to SQLite...')
        index_storage = IndexStorage()
        
        migrated_count = 0
        error_count = 0
        
        # Migrate projects
        projects_dir = utils.safe_join_path('projects')
        if os.path.exists(projects_dir):
            project_files = [f for f in os.listdir(projects_dir) if f.endswith('.md')]
            for filename in project_files:
                project_id = filename[:-3]
                if not utils.validate_id(project_id, 'project'):
                    continue
                
                project_path = utils.safe_join_path('projects', filename)
                try:
                    if self._migrate_entity(project_path, project_id, 'project', None, None, None, index_storage, dry_run):
                        migrated_count += 1
                        self.stdout.write(f'  Migrated project: {project_id}')
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(f'  Error migrating project {project_id}: {e}'))
            
            # Migrate epics, tasks, subtasks
            for project_id in os.listdir(projects_dir):
                project_dir = os.path.join(projects_dir, project_id)
                if not os.path.isdir(project_dir):
                    continue
                
                if not utils.validate_id(project_id, 'project'):
                    continue
                
                # Epics
                epics_dir = os.path.join(project_dir, 'epics')
                if os.path.exists(epics_dir):
                    for epic_filename in os.listdir(epics_dir):
                        if not epic_filename.endswith('.md'):
                            continue
                        epic_id = epic_filename[:-3]
                        if not utils.validate_id(epic_id, 'epic'):
                            continue
                        
                        epic_path = utils.safe_join_path('projects', project_id, 'epics', epic_filename)
                        try:
                            if self._migrate_entity(epic_path, epic_id, 'epic', project_id, None, None, index_storage, dry_run):
                                migrated_count += 1
                                self.stdout.write(f'  Migrated epic: {epic_id}')
                            else:
                                error_count += 1
                        except Exception as e:
                            error_count += 1
                            self.stdout.write(self.style.ERROR(f'  Error migrating epic {epic_id}: {e}'))
                        
                        # Tasks under epic
                        tasks_dir = os.path.join(epics_dir, epic_id, 'tasks')
                        if os.path.exists(tasks_dir):
                            for task_filename in os.listdir(tasks_dir):
                                if not task_filename.endswith('.md'):
                                    continue
                                task_id = task_filename[:-3]
                                if not utils.validate_id(task_id, 'task'):
                                    continue
                                
                                task_path = utils.safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_filename)
                                try:
                                    if self._migrate_entity(task_path, task_id, 'task', project_id, epic_id, None, index_storage, dry_run):
                                        migrated_count += 1
                                        self.stdout.write(f'    Migrated task: {task_id}')
                                    else:
                                        error_count += 1
                                except Exception as e:
                                    error_count += 1
                                    self.stdout.write(self.style.ERROR(f'    Error migrating task {task_id}: {e}'))
                                
                                # Subtasks
                                subtasks_dir = os.path.join(tasks_dir, task_id, 'subtasks')
                                if os.path.exists(subtasks_dir):
                                    for subtask_filename in os.listdir(subtasks_dir):
                                        if not subtask_filename.endswith('.md'):
                                            continue
                                        subtask_id = subtask_filename[:-3]
                                        if not utils.validate_id(subtask_id, 'subtask'):
                                            continue
                                        
                                        subtask_path = utils.safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks', subtask_filename)
                                        try:
                                            if self._migrate_entity(subtask_path, subtask_id, 'subtask', project_id, epic_id, task_id, index_storage, dry_run):
                                                migrated_count += 1
                                                self.stdout.write(f'      Migrated subtask: {subtask_id}')
                                            else:
                                                error_count += 1
                                        except Exception as e:
                                            error_count += 1
                                            self.stdout.write(self.style.ERROR(f'      Error migrating subtask {subtask_id}: {e}'))
                
                # Tasks directly under project (without epic)
                direct_tasks_dir = os.path.join(project_dir, 'tasks')
                if os.path.exists(direct_tasks_dir):
                    for task_filename in os.listdir(direct_tasks_dir):
                        if not task_filename.endswith('.md'):
                            continue
                        task_id = task_filename[:-3]
                        if not utils.validate_id(task_id, 'task'):
                            continue
                        
                        task_path = utils.safe_join_path('projects', project_id, 'tasks', task_filename)
                        try:
                            if self._migrate_entity(task_path, task_id, 'task', project_id, None, None, index_storage, dry_run):
                                migrated_count += 1
                                self.stdout.write(f'  Migrated task (no epic): {task_id}')
                            else:
                                error_count += 1
                        except Exception as e:
                            error_count += 1
                            self.stdout.write(self.style.ERROR(f'  Error migrating task {task_id}: {e}'))
                        
                        # Subtasks under direct tasks
                        subtasks_dir = os.path.join(direct_tasks_dir, task_id, 'subtasks')
                        if os.path.exists(subtasks_dir):
                            for subtask_filename in os.listdir(subtasks_dir):
                                if not subtask_filename.endswith('.md'):
                                    continue
                                subtask_id = subtask_filename[:-3]
                                if not utils.validate_id(subtask_id, 'subtask'):
                                    continue
                                
                                subtask_path = utils.safe_join_path('projects', project_id, 'tasks', task_id, 'subtasks', subtask_filename)
                                try:
                                    if self._migrate_entity(subtask_path, subtask_id, 'subtask', project_id, None, task_id, index_storage, dry_run):
                                        migrated_count += 1
                                        self.stdout.write(f'    Migrated subtask: {subtask_id}')
                                    else:
                                        error_count += 1
                                except Exception as e:
                                    error_count += 1
                                    self.stdout.write(self.style.ERROR(f'    Error migrating subtask {subtask_id}: {e}'))
        
        # Migrate notes
        notes_dir = utils.safe_join_path('notes')
        if os.path.exists(notes_dir):
            note_files = [f for f in os.listdir(notes_dir) if f.endswith('.md')]
            for filename in note_files:
                note_id = filename[:-3]
                note_path = utils.safe_join_path('notes', filename)
                try:
                    if self._migrate_entity(note_path, note_id, 'note', None, None, None, index_storage, dry_run):
                        migrated_count += 1
                        self.stdout.write(f'  Migrated note: {note_id}')
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(f'  Error migrating note {note_id}: {e}'))
        
        # Migrate people
        people_dir = utils.safe_join_path('people')
        if os.path.exists(people_dir):
            people_files = [f for f in os.listdir(people_dir) if f.endswith('.md')]
            for filename in people_files:
                person_id = filename[:-3]
                if not utils.validate_id(person_id, 'person'):
                    continue
                person_path = utils.safe_join_path('people', filename)
                try:
                    if self._migrate_entity(person_path, person_id, 'person', None, None, None, index_storage, dry_run):
                        migrated_count += 1
                        self.stdout.write(f'  Migrated person: {person_id}')
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    self.stdout.write(self.style.ERROR(f'  Error migrating person {person_id}: {e}'))
        
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'\nDry run complete: Would migrate {migrated_count} entities, {error_count} errors'))
        else:
            self.stdout.write(self.style.SUCCESS(f'\nMigration complete: {migrated_count} entities migrated, {error_count} errors'))

    def _migrate_entity(self, file_path, entity_id, entity_type, project_id, epic_id, task_id, index_storage, dry_run):
        """Migrate a single entity from file to SQLite."""
        if not os.path.exists(file_path):
            return False
        
        # Load entity from file
        default_title = f"Untitled {entity_type.title()}"
        default_status = 'active' if entity_type in ['project', 'epic', 'note', 'person'] else 'todo'
        metadata, content = utils.load_entity(file_path, default_title, default_status, metadata_only=False)
        
        if metadata is None:
            return False
        
        # Extract relationship IDs from metadata if not provided
        if project_id is None:
            project_id = metadata.get('project_id')
        if epic_id is None and entity_type in ['task', 'subtask']:
            epic_id = metadata.get('epic_id')
        if task_id is None and entity_type == 'subtask':
            task_id = metadata.get('task_id')
        
        # Ensure relationship IDs are in metadata
        if project_id and 'project_id' not in metadata:
            metadata['project_id'] = project_id
        if epic_id and 'epic_id' not in metadata:
            metadata['epic_id'] = epic_id
        if task_id and 'task_id' not in metadata:
            metadata['task_id'] = task_id
        
        if dry_run:
            return True
        
        # Create or update Entity record
        try:
            with transaction.atomic():
                entity_data = {
                    'type': entity_type,
                    'title': metadata.get('title', default_title),
                    'status': metadata.get('status', default_status),
                    'priority': metadata.get('priority'),
                    'created': metadata.get('created', ''),
                    'updated': metadata.get('updated', ''),
                    'due_date': metadata.get('due_date', ''),
                    'schedule_start': metadata.get('schedule_start', ''),
                    'schedule_end': metadata.get('schedule_end', ''),
                    'project_id': project_id,
                    'epic_id': epic_id,
                    'task_id': task_id,
                    'content': content or '',
                    'metadata_json': json.dumps(metadata),
                }
                
                Entity.objects.update_or_create(
                    id=entity_id,
                    defaults=entity_data
                )
                
                # Sync to search index
                updates_text = ' '.join([
                    u.get('content', '') for u in metadata.get('updates', [])
                ])
                people_tags = metadata.get('people', [])
                labels = metadata.get('labels', [])
                
                index_storage.sync_entity(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    metadata=metadata,
                    content=content,
                    updates_text=updates_text,
                    people_tags=people_tags,
                    labels=labels
                )
                
                return True
        except Exception as e:
            logger.error(f"Error migrating entity {entity_id}: {e}")
            return False
