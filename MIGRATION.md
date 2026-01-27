# Data Migration Guide

This guide explains how to migrate existing data from the markdown file-based storage system to the new SQLite database system.

## Overview

The migration process converts all markdown files with YAML frontmatter into SQLite database records. The migration is **one-way** - after migration, the system uses SQLite as the primary storage and markdown files are no longer used for entity data.

## Prerequisites

1. **Backup your data** - This is critical! The migration reads from markdown files but does not delete them, so you can always roll back if needed.
2. Ensure you have Django 6.0.1+ installed
3. Ensure PyYAML is installed (required for reading existing markdown files)
4. Your virtual environment should be activated

## Migration Steps

### 1. Backup Your Data

Before starting, create a backup of your entire `data/` directory:

```bash
# Create a backup directory
cp -r data data_backup_$(date +%Y%m%d_%H%M%S)

# Or use tar for compression
tar -czf data_backup_$(date +%Y%m%d_%H%M%S).tar.gz data/
```

### 2. Ensure Database is Up to Date

Run Django migrations to ensure your database schema is current:

```bash
# Activate virtual environment (if not already active)
source venv/bin/activate  # On Linux/Mac
# OR
venv\Scripts\activate     # On Windows

# Run migrations
python manage.py migrate
```

This will apply the migration that adds the `content` field and removes file tracking fields from the `Entity` model.

### 3. Run the Migration Command

The migration command reads all markdown files and imports them into SQLite:

```bash
# Dry run first (recommended) - shows what would be migrated without making changes
python manage.py migrate_to_sqlite --dry-run

# Actual migration
python manage.py migrate_to_sqlite
```

#### Migration Command Options

- `--dry-run`: Preview what will be migrated without making any changes
- `--verbose`: Show detailed output for each file processed
- `--skip-existing`: Skip entities that already exist in the database (useful for re-running)

### 4. Verify the Migration

After migration, verify that all data was imported correctly:

```bash
# Check entity counts
python manage.py shell
```

In the Django shell:

```python
from pm.models import Entity

# Count entities by type
print(f"Projects: {Entity.objects.filter(type='project').count()}")
print(f"Epics: {Entity.objects.filter(type='epic').count()}")
print(f"Tasks: {Entity.objects.filter(type='task').count()}")
print(f"Subtasks: {Entity.objects.filter(type='subtask').count()}")
print(f"Notes: {Entity.objects.filter(type='note').count()}")
print(f"People: {Entity.objects.filter(type='person').count()}")

# Verify a specific entity
project = Entity.objects.filter(type='project').first()
if project:
    print(f"Sample project: {project.title}")
    print(f"Has content: {bool(project.content)}")
    print(f"Has metadata: {bool(project.metadata_json)}")
```

### 5. Test the Application

Start the development server and test key functionality:

```bash
python manage.py runserver
```

Test the following:
- View project list
- Open a project detail page
- View epics, tasks, and subtasks
- Create a new entity
- Edit an existing entity
- Search functionality
- Notes and people pages

### 6. (Optional) Clean Up Old Files

**Important**: Only do this after you've verified everything works correctly and you're confident the migration was successful.

The markdown files are no longer used by the system, but they remain on disk as a backup. You can:

1. **Keep them as backup** (recommended) - Leave the files in place
2. **Archive them** - Move to a backup location:
   ```bash
   mkdir -p data_backup/markdown_files
   mv data/projects data_backup/markdown_files/
   mv data/notes data_backup/markdown_files/
   mv data/people data_backup/markdown_files/
   ```
3. **Delete them** - Only if you're absolutely certain:
   ```bash
   # WARNING: This is permanent!
   rm -rf data/projects data/notes data/people
   ```

## What Gets Migrated

The migration process imports:

- **Projects** - All project markdown files
- **Epics** - All epic files within projects
- **Tasks** - All task files (both under epics and directly under projects)
- **Subtasks** - All subtask files
- **Notes** - All note files
- **People** - All person files

For each entity, the migration:
- Extracts YAML frontmatter → stored in `metadata_json` field
- Extracts markdown content → stored in `content` field
- Preserves all relationships (project_id, epic_id, task_id)
- Syncs to the search index (FTS5)

## Troubleshooting

### Migration Fails with "Entity already exists"

If you see errors about entities already existing:

```bash
# Use --skip-existing to skip duplicates
python manage.py migrate_to_sqlite --skip-existing
```

Or manually clean the database:

```bash
python manage.py shell
```

```python
from pm.models import Entity
# Delete all entities (WARNING: This deletes all data!)
Entity.objects.all().delete()
```

### Missing Entities After Migration

If some entities are missing:

1. Check the migration output for errors
2. Verify the markdown files exist in the expected locations
3. Check file permissions
4. Re-run with `--verbose` to see detailed output

### Search Not Working

After migration, ensure the search index is synced:

```bash
python manage.py shell
```

```python
from pm.storage.index_storage import IndexStorage
from pm.models import Entity

# Re-sync all entities to search index
index_storage = IndexStorage()
for entity in Entity.objects.all():
    try:
        import json
        metadata = json.loads(entity.metadata_json) if entity.metadata_json else {}
        index_storage.sync_entity(
            entity.id,
            entity.type,
            metadata,
            content=entity.content or ''
        )
    except Exception as e:
        print(f"Error syncing {entity.id}: {e}")
```

## Rollback Procedure

If you need to rollback to the markdown file system:

1. **Restore your backup**:
   ```bash
   # If you used cp
   rm -rf data/projects data/notes data/people
   cp -r data_backup_YYYYMMDD_HHMMSS/* data/

   # If you used tar
   tar -xzf data_backup_YYYYMMDD_HHMMSS.tar.gz
   ```

2. **Revert the database migration**:
   ```bash
   python manage.py migrate pm 0001_initial
   ```

3. **Restore the old code** - Check out the commit before the SQLite migration

**Note**: The current codebase no longer supports markdown file-based storage. To rollback, you would need to restore an older version of the codebase as well.

## Post-Migration

After successful migration:

1. ✅ All entity data is now in SQLite
2. ✅ Markdown files are no longer used (but kept as backup)
3. ✅ Search uses FTS5 index
4. ✅ All views use database queries instead of file operations
5. ✅ PyYAML is no longer needed for normal operation (only for migration script)

## Support

If you encounter issues during migration:

1. Check the migration command output for errors
2. Review Django logs in `logs/django.log`
3. Verify your markdown files are valid (proper YAML frontmatter)
4. Ensure all required fields are present in metadata

## Migration Script Details

The migration script (`pm/management/commands/migrate_to_sqlite.py`):

- Scans the `data/` directory structure
- Reads each markdown file
- Parses YAML frontmatter and markdown content
- Creates/updates Entity records in SQLite
- Syncs to the search index
- Handles relationships and foreign keys
- Provides progress output and error reporting

The script is idempotent - you can run it multiple times safely (use `--skip-existing` to avoid duplicates).
