# Data migration to populate specialized entity models from Entity table

from django.db import migrations


def migrate_entities_to_specialized_models(apps, schema_editor):
    """Copy data from Entity table to specialized model tables."""
    Entity = apps.get_model('pm', 'Entity')
    Project = apps.get_model('pm', 'Project')
    Epic = apps.get_model('pm', 'Epic')
    Task = apps.get_model('pm', 'Task')
    Subtask = apps.get_model('pm', 'Subtask')
    Note = apps.get_model('pm', 'Note')
    EntityPerson = apps.get_model('pm', 'EntityPerson')
    EntityLabel = apps.get_model('pm', 'EntityLabel')
    EntityPersonLink = apps.get_model('pm', 'EntityPersonLink')
    EntityLabelLink = apps.get_model('pm', 'EntityLabelLink')
    
    print(f"\n🔄 Starting entity migration...")
    
    # First pass: Create all projects (no dependencies)
    projects = Entity.objects.filter(type='project')
    print(f"  Migrating {projects.count()} projects...")
    project_map = {}
    for entity in projects:
        project = Project.objects.create(
            id=entity.id,
            title=entity.title,
            status_fk=entity.status_fk,
            priority=entity.priority,
            created=entity.created,
            updated=entity.updated,
            due_date_dt=entity.due_date_dt,
            schedule_start_dt=entity.schedule_start_dt,
            schedule_end_dt=entity.schedule_end_dt,
            content=entity.content,
            seq_id=entity.seq_id,
            archived=entity.archived,
            color=entity.color,
            stats=entity.stats,
            stats_version=entity.stats_version,
            stats_updated=entity.stats_updated,
            notes=entity.notes,
        )
        project_map[entity.id] = project
    print(f"  ✓ Created {len(project_map)} projects")
    
    # Second pass: Create epics (depend on projects)
    epics = Entity.objects.filter(type='epic')
    print(f"  Migrating {epics.count()} epics...")
    epic_map = {}
    skipped_epics = 0
    for entity in epics:
        if entity.project_id and entity.project_id in project_map:
            epic = Epic.objects.create(
                id=entity.id,
                title=entity.title,
                status_fk=entity.status_fk,
                priority=entity.priority,
                created=entity.created,
                updated=entity.updated,
                due_date_dt=entity.due_date_dt,
                schedule_start_dt=entity.schedule_start_dt,
                schedule_end_dt=entity.schedule_end_dt,
                content=entity.content,
                seq_id=entity.seq_id,
                archived=entity.archived,
                project=project_map[entity.project_id],
                is_inbox_epic=entity.is_inbox_epic,
                notes=entity.notes,
            )
            epic_map[entity.id] = epic
        else:
            print(f"    ⚠️  Skipping epic {entity.id} - invalid project_id: {entity.project_id}")
            skipped_epics += 1
    print(f"  ✓ Created {len(epic_map)} epics (skipped {skipped_epics})")
    
    # Third pass: Create tasks (depend on projects, optionally on epics)
    tasks = Entity.objects.filter(type='task')
    print(f"  Migrating {tasks.count()} tasks...")
    task_map = {}
    skipped_tasks = 0
    for entity in tasks:
        if not entity.project_id or entity.project_id not in project_map:
            print(f"    ⚠️  Skipping task {entity.id} - invalid project_id: {entity.project_id}")
            skipped_tasks += 1
            continue
        
        # Epic is optional - only set if it exists and is valid
        epic = None
        if entity.epic_id:
            if entity.epic_id in epic_map:
                epic = epic_map[entity.epic_id]
            else:
                print(f"    ⚠️  Task {entity.id} references invalid epic_id: {entity.epic_id} - setting to None")
        
        task = Task.objects.create(
            id=entity.id,
            title=entity.title,
            status_fk=entity.status_fk,
            priority=entity.priority,
            created=entity.created,
            updated=entity.updated,
            due_date_dt=entity.due_date_dt,
            schedule_start_dt=entity.schedule_start_dt,
            schedule_end_dt=entity.schedule_end_dt,
            content=entity.content,
            seq_id=entity.seq_id,
            archived=entity.archived,
            project=project_map[entity.project_id],
            epic=epic,
            dependencies=entity.dependencies,
            checklist=entity.checklist,
            notes=entity.notes,
        )
        task_map[entity.id] = task
    print(f"  ✓ Created {len(task_map)} tasks (skipped {skipped_tasks})")
    
    # Fourth pass: Create subtasks (depend on tasks, projects, optionally epics)
    subtasks = Entity.objects.filter(type='subtask')
    print(f"  Migrating {subtasks.count()} subtasks...")
    subtask_count = 0
    skipped_subtasks = 0
    for entity in subtasks:
        if not entity.task_id or entity.task_id not in task_map:
            print(f"    ⚠️  Skipping subtask {entity.id} - invalid task_id: {entity.task_id}")
            skipped_subtasks += 1
            continue
        
        if not entity.project_id or entity.project_id not in project_map:
            print(f"    ⚠️  Skipping subtask {entity.id} - invalid project_id: {entity.project_id}")
            skipped_subtasks += 1
            continue
        
        # Epic is optional
        epic = None
        if entity.epic_id and entity.epic_id in epic_map:
            epic = epic_map[entity.epic_id]
        
        Subtask.objects.create(
            id=entity.id,
            title=entity.title,
            status_fk=entity.status_fk,
            priority=entity.priority,
            created=entity.created,
            updated=entity.updated,
            due_date_dt=entity.due_date_dt,
            schedule_start_dt=entity.schedule_start_dt,
            schedule_end_dt=entity.schedule_end_dt,
            content=entity.content,
            seq_id=entity.seq_id,
            archived=entity.archived,
            task=task_map[entity.task_id],
            project=project_map[entity.project_id],
            epic=epic,
            checklist=entity.checklist,
            notes=entity.notes,
        )
        subtask_count += 1
    print(f"  ✓ Created {subtask_count} subtasks (skipped {skipped_subtasks})")
    
    # Fifth pass: Create notes
    notes = Entity.objects.filter(type='note')
    print(f"  Migrating {notes.count()} notes...")
    note_count = 0
    for entity in notes:
        Note.objects.create(
            id=entity.id,
            title=entity.title,
            status_fk=entity.status_fk,
            priority=entity.priority,
            created=entity.created,
            updated=entity.updated,
            due_date_dt=entity.due_date_dt,
            schedule_start_dt=entity.schedule_start_dt,
            schedule_end_dt=entity.schedule_end_dt,
            content=entity.content,
            seq_id=entity.seq_id,
            archived=entity.archived,
            notes=entity.notes,
        )
        note_count += 1
    print(f"  ✓ Created {note_count} notes")
    
    # Migrate many-to-many relationships
    print(f"\n🔄 Migrating many-to-many relationships...")
    
    # Get ContentType instances for the new models
    # In migrations, we need to get ContentType from the database, not via get_for_model
    ContentTypeModel = apps.get_model('contenttypes', 'ContentType')
    
    # Create a mapping of entity IDs to ContentTypes by looking them up
    content_type_map = {}
    try:
        content_type_map['project'] = ContentTypeModel.objects.get(app_label='pm', model='project')
    except ContentTypeModel.DoesNotExist:
        print("    ⚠️  ContentType for Project not found, creating it")
        content_type_map['project'] = ContentTypeModel.objects.create(app_label='pm', model='project')
    
    try:
        content_type_map['epic'] = ContentTypeModel.objects.get(app_label='pm', model='epic')
    except ContentTypeModel.DoesNotExist:
        print("    ⚠️  ContentType for Epic not found, creating it")
        content_type_map['epic'] = ContentTypeModel.objects.create(app_label='pm', model='epic')
    
    try:
        content_type_map['task'] = ContentTypeModel.objects.get(app_label='pm', model='task')
    except ContentTypeModel.DoesNotExist:
        print("    ⚠️  ContentType for Task not found, creating it")
        content_type_map['task'] = ContentTypeModel.objects.create(app_label='pm', model='task')
    
    try:
        content_type_map['subtask'] = ContentTypeModel.objects.get(app_label='pm', model='subtask')
    except ContentTypeModel.DoesNotExist:
        print("    ⚠️  ContentType for Subtask not found, creating it")
        content_type_map['subtask'] = ContentTypeModel.objects.create(app_label='pm', model='subtask')
    
    try:
        content_type_map['note'] = ContentTypeModel.objects.get(app_label='pm', model='note')
    except ContentTypeModel.DoesNotExist:
        print("    ⚠️  ContentType for Note not found, creating it")
        content_type_map['note'] = ContentTypeModel.objects.create(app_label='pm', model='note')
    
    # Migrate EntityPerson to EntityPersonLink
    person_links = EntityPerson.objects.all()
    print(f"  Migrating {person_links.count()} person assignments...")
    person_link_count = 0
    for ep in person_links:
        entity_type = Entity.objects.get(id=ep.entity_id).type
        if entity_type in content_type_map:
            EntityPersonLink.objects.create(
                content_type=content_type_map[entity_type],
                object_id=ep.entity_id,
                person=ep.person,
                created=ep.created,
            )
            person_link_count += 1
    print(f"  ✓ Created {person_link_count} person links")
    
    # Migrate EntityLabel to EntityLabelLink
    label_links = EntityLabel.objects.all()
    print(f"  Migrating {label_links.count()} label assignments...")
    label_link_count = 0
    for el in label_links:
        entity_type = Entity.objects.get(id=el.entity_id).type
        if entity_type in content_type_map:
            EntityLabelLink.objects.create(
                content_type=content_type_map[entity_type],
                object_id=el.entity_id,
                label=el.label,
                created=el.created,
            )
            label_link_count += 1
    print(f"  ✓ Created {label_link_count} label links")
    
    print(f"\n✅ Migration complete!")
    print(f"  Total: {len(project_map)} projects, {len(epic_map)} epics, {len(task_map)} tasks, {subtask_count} subtasks, {note_count} notes")
    print(f"  Relationships: {person_link_count} person links, {label_link_count} label links")


def reverse_migration(apps, schema_editor):
    """Reverse the migration by clearing specialized model tables."""
    Project = apps.get_model('pm', 'Project')
    Epic = apps.get_model('pm', 'Epic')
    Task = apps.get_model('pm', 'Task')
    Subtask = apps.get_model('pm', 'Subtask')
    Note = apps.get_model('pm', 'Note')
    EntityPersonLink = apps.get_model('pm', 'EntityPersonLink')
    EntityLabelLink = apps.get_model('pm', 'EntityLabelLink')
    
    print("\n🔄 Reversing entity migration...")
    EntityPersonLink.objects.all().delete()
    EntityLabelLink.objects.all().delete()
    Subtask.objects.all().delete()
    Task.objects.all().delete()
    Epic.objects.all().delete()
    Project.objects.all().delete()
    Note.objects.all().delete()
    print("✅ Reverse migration complete!")


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0016_create_specialized_entity_models'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.RunPython(migrate_entities_to_specialized_models, reverse_migration),
    ]
