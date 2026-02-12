"""
Management command to verify search index consistency.

Compares entities in the database with entries in the search_index table
to identify orphaned entries or missing index entries.
"""
from django.core.management.base import BaseCommand
from django.db import connection
from pm.models import Project, Epic, Task, Subtask, Note
from pm.storage.index_storage import IndexStorage


class Command(BaseCommand):
    help = 'Verify search index consistency and optionally fix issues'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Automatically fix any inconsistencies found',
        )

    def handle(self, *args, **options):
        fix_issues = options.get('fix', False)
        
        self.stdout.write('Verifying search index consistency...\n')
        
        # Get all entity IDs from database
        db_entities = {}
        db_entities['project'] = set(Project.objects.values_list('id', flat=True))
        db_entities['epic'] = set(Epic.objects.values_list('id', flat=True))
        db_entities['task'] = set(Task.objects.values_list('id', flat=True))
        db_entities['subtask'] = set(Subtask.objects.values_list('id', flat=True))
        db_entities['note'] = set(Note.objects.values_list('id', flat=True))
        
        total_db_entities = sum(len(ids) for ids in db_entities.values())
        self.stdout.write(f'Found {total_db_entities} entities in database:')
        for entity_type, ids in db_entities.items():
            self.stdout.write(f'  - {len(ids)} {entity_type}s')
        
        # Get all entity IDs from search index
        with connection.cursor() as cursor:
            cursor.execute("SELECT entity_id, entity_type FROM search_index")
            search_index_entries = cursor.fetchall()
        
        search_entities = {}
        for entity_id, entity_type in search_index_entries:
            if entity_type not in search_entities:
                search_entities[entity_type] = set()
            search_entities[entity_type].add(entity_id)
        
        total_search_entities = sum(len(ids) for ids in search_entities.values())
        self.stdout.write(f'\nFound {total_search_entities} entries in search index:')
        for entity_type, ids in search_entities.items():
            self.stdout.write(f'  - {len(ids)} {entity_type}s')
        
        # Find orphaned entries (in search index but not in database)
        orphaned = {}
        for entity_type, search_ids in search_entities.items():
            db_ids = db_entities.get(entity_type, set())
            orphaned_ids = search_ids - db_ids
            if orphaned_ids:
                orphaned[entity_type] = orphaned_ids
        
        # Find missing entries (in database but not in search index)
        missing = {}
        for entity_type, db_ids in db_entities.items():
            search_ids = search_entities.get(entity_type, set())
            missing_ids = db_ids - search_ids
            if missing_ids:
                missing[entity_type] = missing_ids
        
        # Report findings
        self.stdout.write('\n' + '='*60)
        if not orphaned and not missing:
            self.stdout.write(self.style.SUCCESS('\n✓ Search index is consistent!'))
            self.stdout.write('  No orphaned or missing entries found.\n')
            return
        
        if orphaned:
            total_orphaned = sum(len(ids) for ids in orphaned.values())
            self.stdout.write(self.style.ERROR(f'\n✗ Found {total_orphaned} orphaned search index entries:'))
            for entity_type, ids in orphaned.items():
                self.stdout.write(f'  - {len(ids)} {entity_type}s: {", ".join(sorted(list(ids)[:5]))}{"..." if len(ids) > 5 else ""}')
        
        if missing:
            total_missing = sum(len(ids) for ids in missing.values())
            self.stdout.write(self.style.WARNING(f'\n⚠ Found {total_missing} entities missing from search index:'))
            for entity_type, ids in missing.items():
                self.stdout.write(f'  - {len(ids)} {entity_type}s: {", ".join(sorted(list(ids)[:5]))}{"..." if len(ids) > 5 else ""}')
        
        # Fix issues if requested
        if fix_issues:
            self.stdout.write('\n' + '='*60)
            self.stdout.write('Fixing inconsistencies...\n')
            
            index_storage = IndexStorage()
            fixed_count = 0
            
            # Remove orphaned entries
            if orphaned:
                self.stdout.write('\nRemoving orphaned search index entries...')
                with connection.cursor() as cursor:
                    for entity_type, ids in orphaned.items():
                        for entity_id in ids:
                            cursor.execute("DELETE FROM search_index WHERE entity_id = %s", [entity_id])
                            cursor.execute("DELETE FROM updates WHERE entity_id = %s", [entity_id])
                            fixed_count += 1
                            self.stdout.write(f'  Removed: {entity_type} {entity_id}')
            
            # Add missing entries
            if missing:
                self.stdout.write('\nAdding missing search index entries...')
                from pm.views import _build_metadata_from_entity, _merge_people_from_entityperson
                
                model_map = {
                    'project': Project,
                    'epic': Epic,
                    'task': Task,
                    'subtask': Subtask,
                    'note': Note,
                }
                
                for entity_type, ids in missing.items():
                    model_class = model_map[entity_type]
                    for entity_id in ids:
                        try:
                            # Get entity from database
                            entity = model_class.objects.get(id=entity_id)
                            
                            # Build metadata
                            metadata = _build_metadata_from_entity(entity)
                            metadata = _merge_people_from_entityperson(entity, metadata)
                            
                            # Extract updates, people, labels
                            updates_text = ' '.join([u.get('content', '') for u in metadata.get('updates', [])])
                            people_tags = metadata.get('people', [])
                            labels = metadata.get('labels', [])
                            
                            # Sync to search index (full sync including database)
                            index_storage.sync_entity(
                                entity_id=entity_id,
                                entity_type=entity_type,
                                metadata=metadata,
                                content=entity.content or '',
                                updates_text=updates_text,
                                people_tags=people_tags,
                                labels=labels
                            )
                            fixed_count += 1
                            self.stdout.write(f'  Added: {entity_type} {entity_id} - {entity.title[:50]}')
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f'  Error adding {entity_type} {entity_id}: {e}'))
            
            self.stdout.write(self.style.SUCCESS(f'\n✓ Fixed {fixed_count} inconsistencies!'))
        else:
            self.stdout.write('\n' + '='*60)
            self.stdout.write('\nRun with --fix flag to automatically fix these issues:')
            self.stdout.write('  python manage.py verify_search_index --fix\n')
