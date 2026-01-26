"""
Management command to sync all markdown files to SQLite index.
"""
from django.core.management.base import BaseCommand
import os
import logging
from pm import utils
from pm.storage import SyncManager

logger = logging.getLogger('pm')


class Command(BaseCommand):
    help = 'Sync all markdown files to SQLite index'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-sync even if index is up to date',
        )

    def handle(self, *args, **options):
        sync_manager = SyncManager()
        force = options.get('force', False)
        
        self.stdout.write('Starting index sync...')
        
        # Sync projects
        projects_dir = utils.safe_join_path('projects')
        if os.path.exists(projects_dir):
            project_files = [f for f in os.listdir(projects_dir) if f.endswith('.md')]
            for filename in project_files:
                project_id = filename[:-3]
                if utils.validate_id(project_id, 'project'):
                    project_path = utils.safe_join_path('projects', filename)
                    try:
                        sync_manager.sync_entity_to_index(project_path, project_id, 'project')
                        self.stdout.write(f'  Synced project: {project_id}')
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f'  Error syncing project {project_id}: {e}'))
            
            # Sync epics, tasks, subtasks
            for project_id in os.listdir(projects_dir):
                project_dir = os.path.join(projects_dir, project_id)
                if not os.path.isdir(project_dir):
                    continue
                
                if not utils.validate_id(project_id, 'project'):
                    continue
                
                epics_dir = os.path.join(project_dir, 'epics')
                if os.path.exists(epics_dir):
                    for epic_filename in os.listdir(epics_dir):
                        if not epic_filename.endswith('.md'):
                            continue
                        epic_id = epic_filename[:-3]
                        if utils.validate_id(epic_id, 'epic'):
                            epic_path = utils.safe_join_path('projects', project_id, 'epics', epic_filename)
                            try:
                                sync_manager.sync_entity_to_index(epic_path, epic_id, 'epic')
                                self.stdout.write(f'  Synced epic: {epic_id}')
                            except Exception as e:
                                self.stdout.write(self.style.ERROR(f'  Error syncing epic {epic_id}: {e}'))
                        
                        # Tasks
                        tasks_dir = os.path.join(epics_dir, epic_id, 'tasks')
                        if os.path.exists(tasks_dir):
                            for task_filename in os.listdir(tasks_dir):
                                if not task_filename.endswith('.md'):
                                    continue
                                task_id = task_filename[:-3]
                                if utils.validate_id(task_id, 'task'):
                                    task_path = utils.safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_filename)
                                    try:
                                        sync_manager.sync_entity_to_index(task_path, task_id, 'task')
                                        self.stdout.write(f'    Synced task: {task_id}')
                                    except Exception as e:
                                        self.stdout.write(self.style.ERROR(f'    Error syncing task {task_id}: {e}'))
                                
                                # Subtasks
                                subtasks_dir = os.path.join(tasks_dir, task_id, 'subtasks')
                                if os.path.exists(subtasks_dir):
                                    for subtask_filename in os.listdir(subtasks_dir):
                                        if not subtask_filename.endswith('.md'):
                                            continue
                                        subtask_id = subtask_filename[:-3]
                                        if utils.validate_id(subtask_id, 'subtask'):
                                            subtask_path = utils.safe_join_path('projects', project_id, 'epics', epic_id, 'tasks', task_id, 'subtasks', subtask_filename)
                                            try:
                                                sync_manager.sync_entity_to_index(subtask_path, subtask_id, 'subtask')
                                                self.stdout.write(f'      Synced subtask: {subtask_id}')
                                            except Exception as e:
                                                self.stdout.write(self.style.ERROR(f'      Error syncing subtask {subtask_id}: {e}'))
        
        # Sync notes
        notes_dir = utils.safe_join_path('notes')
        if os.path.exists(notes_dir):
            note_files = [f for f in os.listdir(notes_dir) if f.endswith('.md')]
            for filename in note_files:
                note_id = filename[:-3]
                note_path = utils.safe_join_path('notes', filename)
                try:
                    sync_manager.sync_entity_to_index(note_path, note_id, 'note')
                    self.stdout.write(f'  Synced note: {note_id}')
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'  Error syncing note {note_id}: {e}'))
        
        self.stdout.write(self.style.SUCCESS('Index sync complete!'))
