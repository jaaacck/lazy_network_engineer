from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Update, Status, Person, Label,
    Project, Epic, Task, Subtask, Note,
    EntityPersonLink, EntityLabelLink
)

# Customize admin site
admin.site.site_header = "Lazy Network Engineer Admin"
admin.site.site_title = "LNE Admin"
admin.site.index_title = "Backend Management"


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'status_fk', 'priority', 'color', 'created', 'updated')
    list_filter = ('status_fk', 'priority', 'archived')
    search_fields = ('id', 'title', 'content')
    readonly_fields = ('id', 'created', 'updated')


@admin.register(Epic)
class EpicAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'status_fk', 'priority', 'is_inbox_epic', 'created', 'updated')
    list_filter = ('status_fk', 'priority', 'is_inbox_epic', 'archived')
    search_fields = ('id', 'title', 'content')
    readonly_fields = ('id', 'created', 'updated')
    raw_id_fields = ('project',)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'project', 'epic', 'status_fk', 'priority', 'created', 'updated')
    list_filter = ('status_fk', 'priority', 'archived')
    search_fields = ('id', 'title', 'content')
    readonly_fields = ('id', 'created', 'updated')
    raw_id_fields = ('project', 'epic')


@admin.register(Subtask)
class SubtaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'task', 'project', 'epic', 'status_fk', 'priority', 'created', 'updated')
    list_filter = ('status_fk', 'priority', 'archived')
    search_fields = ('id', 'title', 'content')
    readonly_fields = ('id', 'created', 'updated')
    raw_id_fields = ('task', 'project', 'epic')


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'status_fk', 'priority', 'created', 'updated')
    list_filter = ('status_fk', 'priority', 'archived')
    search_fields = ('id', 'title', 'content')
    readonly_fields = ('id', 'created', 'updated')



@admin.register(Status)
class StatusAdmin(admin.ModelAdmin):
    list_display = ('name', 'display_name', 'entity_types', 'order', 'is_active', 'created')
    list_filter = ('is_active', 'entity_types')
    search_fields = ('name', 'display_name')
    ordering = ('order', 'name')


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ('name', 'display_name', 'email', 'created', 'updated')
    search_fields = ('name', 'display_name', 'email')
    readonly_fields = ('id', 'created', 'updated')


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ('name', 'created')
    search_fields = ('name',)
    readonly_fields = ('id', 'created')


@admin.register(EntityPersonLink)
class EntityPersonLinkAdmin(admin.ModelAdmin):
    list_display = ('person', 'content_type', 'object_id', 'created')
    list_filter = ('content_type', 'created')
    search_fields = ('person__name',)
    readonly_fields = ('created',)


@admin.register(EntityLabelLink)
class EntityLabelLinkAdmin(admin.ModelAdmin):
    list_display = ('label', 'content_type', 'object_id', 'created')
    list_filter = ('content_type', 'created')
    search_fields = ('label__name',)
    readonly_fields = ('created',)


@admin.register(Update)
class UpdateAdmin(admin.ModelAdmin):
    list_display = ('id', 'entity_id', 'type', 'activity_type', 'timestamp', 'content_preview')
    list_filter = ('type', 'activity_type', 'timestamp')
    search_fields = ('entity_id', 'content')
    readonly_fields = ('id', 'timestamp')
    
    def content_preview(self, obj):
        """Show first 100 characters of content"""
        if len(obj.content) > 100:
            return obj.content[:100] + '...'
        return obj.content
    content_preview.short_description = 'Content'
