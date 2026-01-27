# Generated migration to extract metadata fields from JSON

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pm', '0004_migrate_status_person_data'),
    ]

    operations = [
        # Create Label model
        migrations.CreateModel(
            name='Label',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=200, unique=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'labels',
            },
        ),
        migrations.AddIndex(
            model_name='label',
            index=models.Index(fields=['name'], name='labels_name_idx'),
        ),
        # Create EntityLabel model
        migrations.CreateModel(
            name='EntityLabel',
            fields=[
                ('id', models.AutoField(primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('entity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='labels', to='pm.entity')),
                ('label', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entities', to='pm.label')),
            ],
            options={
                'db_table': 'entity_labels',
                'unique_together': {('entity', 'label')},
            },
        ),
        migrations.AddIndex(
            model_name='entitylabel',
            index=models.Index(fields=['entity'], name='entity_labels_entity_idx'),
        ),
        migrations.AddIndex(
            model_name='entitylabel',
            index=models.Index(fields=['label'], name='entity_labels_label_idx'),
        ),
        # Add new fields to Entity model
        migrations.AddField(
            model_name='entity',
            name='seq_id',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='entity',
            name='archived',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='entity',
            name='is_inbox_epic',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='entity',
            name='color',
            field=models.CharField(blank=True, max_length=7, null=True),
        ),
        migrations.AddField(
            model_name='entity',
            name='notes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='entity',
            name='dependencies',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='entity',
            name='checklist',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='entity',
            name='stats',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='entity',
            name='stats_version',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='entity',
            name='stats_updated',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='entity',
            index=models.Index(fields=['archived'], name='entities_archived_idx'),
        ),
        migrations.AddIndex(
            model_name='entity',
            index=models.Index(fields=['seq_id'], name='entities_seq_id_idx'),
        ),
    ]
