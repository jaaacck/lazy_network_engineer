# Generated migration to add Status, Person, EntityPerson models and new Entity fields

from django.db import migrations, models
import django.db.models.deletion


def seed_status_data(apps, schema_editor):
    """Seed Status table with all existing status values."""
    Status = apps.get_model('pm', 'Status')
    
    # Statuses for Projects/Epics
    Status.objects.get_or_create(
        name='active',
        defaults={
            'display_name': 'Active',
            'entity_types': 'project,epic,note,person',
            'order': 1,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='completed',
        defaults={
            'display_name': 'Completed',
            'entity_types': 'project,epic',
            'order': 2,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='canceled',
        defaults={
            'display_name': 'Canceled',
            'entity_types': 'project,epic',
            'order': 3,
            'is_active': True,
        }
    )
    
    # Statuses for Tasks/Subtasks
    Status.objects.get_or_create(
        name='todo',
        defaults={
            'display_name': 'Todo',
            'entity_types': 'task,subtask',
            'order': 1,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='next',
        defaults={
            'display_name': 'Next',
            'entity_types': 'task,subtask',
            'order': 2,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='in_progress',
        defaults={
            'display_name': 'In Progress',
            'entity_types': 'task,subtask',
            'order': 3,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='on_hold',
        defaults={
            'display_name': 'On Hold',
            'entity_types': 'task,subtask',
            'order': 4,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='blocked',
        defaults={
            'display_name': 'Blocked',
            'entity_types': 'task,subtask',
            'order': 5,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='done',
        defaults={
            'display_name': 'Done',
            'entity_types': 'task,subtask',
            'order': 6,
            'is_active': True,
        }
    )
    Status.objects.get_or_create(
        name='cancelled',
        defaults={
            'display_name': 'Cancelled',
            'entity_types': 'task,subtask',
            'order': 7,
            'is_active': True,
        }
    )


def migrate_person_entities(apps, schema_editor):
    """Migrate person entities from Entity table to Person table."""
    Entity = apps.get_model('pm', 'Entity')
    Person = apps.get_model('pm', 'Person')
    import json
    
    # Get all person entities
    person_entities = Entity.objects.filter(type='person')
    
    for entity in person_entities:
        try:
            metadata = json.loads(entity.metadata_json) if entity.metadata_json else {}
            person_name = metadata.get('name', '').strip().lstrip('@')
            
            if person_name:
                # Create Person record
                Person.objects.get_or_create(
                    id=entity.id,
                    defaults={
                        'name': person_name,
                        'display_name': person_name,
                        'metadata_json': entity.metadata_json,
                    }
                )
        except (json.JSONDecodeError, TypeError):
            # Skip entities with invalid JSON
            continue


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0002_add_content_remove_file_tracking'),
    ]

    operations = [
        # Create Status model
        migrations.CreateModel(
            name='Status',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=50, unique=True)),
                ('display_name', models.CharField(max_length=100)),
                ('entity_types', models.CharField(max_length=200)),
                ('color', models.CharField(blank=True, max_length=7)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'statuses',
                'ordering': ['order', 'name'],
            },
        ),
        # Create Person model
        migrations.CreateModel(
            name='Person',
            fields=[
                ('id', models.CharField(max_length=50, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200, unique=True)),
                ('display_name', models.CharField(blank=True, max_length=200)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('metadata_json', models.TextField(blank=True)),
            ],
            options={
                'db_table': 'persons',
            },
        ),
        # Add index for Person.name
        migrations.AddIndex(
            model_name='person',
            index=models.Index(fields=['name'], name='persons_name_idx'),
        ),
        # Add new fields to Entity
        migrations.AddField(
            model_name='entity',
            name='status_fk',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='entities', to='pm.status'),
        ),
        migrations.AddField(
            model_name='entity',
            name='due_date_dt',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='entity',
            name='schedule_start_dt',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='entity',
            name='schedule_end_dt',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Add index for status_fk
        migrations.AddIndex(
            model_name='entity',
            index=models.Index(fields=['status_fk'], name='entities_status_fk_idx'),
        ),
        # Add index for due_date_dt
        migrations.AddIndex(
            model_name='entity',
            index=models.Index(fields=['due_date_dt'], name='entities_due_date_dt_idx'),
        ),
        # Create EntityPerson model
        migrations.CreateModel(
            name='EntityPerson',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('entity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assigned_people', to='pm.entity')),
                ('person', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assigned_entities', to='pm.person')),
            ],
            options={
                'db_table': 'entity_persons',
                'unique_together': {('entity', 'person')},
            },
        ),
        # Add indexes for EntityPerson
        migrations.AddIndex(
            model_name='entityperson',
            index=models.Index(fields=['entity'], name='entity_persons_entity_idx'),
        ),
        migrations.AddIndex(
            model_name='entityperson',
            index=models.Index(fields=['person'], name='entity_persons_person_idx'),
        ),
        # Seed status data
        migrations.RunPython(seed_status_data, migrations.RunPython.noop),
        # Migrate person entities
        migrations.RunPython(migrate_person_entities, migrations.RunPython.noop),
    ]
