from django.contrib import admin
from django.utils.html import format_html
from .models import Entity, Update, Status, Person, EntityPerson
import json

# Customize admin site
admin.site.site_header = "Lazy Network Engineer Admin"
admin.site.site_title = "LNE Admin"
admin.site.index_title = "Backend Management"


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ('id', 'type', 'title', 'status_fk', 'priority', 'created', 'updated')
    list_filter = ('type', 'status_fk', 'priority')
    search_fields = ('id', 'title', 'content')
    readonly_fields = ('id', 'created', 'updated', 'formatted_metadata')
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'type', 'title', 'status_fk', 'priority')
        }),
        ('Relationships', {
            'fields': ('project_id', 'epic_id', 'task_id'),
            'classes': ('collapse',)
        }),
        ('Scheduling', {
            'fields': ('due_date', 'due_date_dt', 'schedule_start', 'schedule_start_dt', 'schedule_end', 'schedule_end_dt'),
            'classes': ('collapse',)
        }),
        ('Content', {
            'fields': ('content',)
        }),
        ('Metadata', {
            'fields': ('formatted_metadata', 'metadata_json'),
            'classes': ('collapse',)
        }),
    )
    
    def formatted_metadata(self, obj):
        """Display formatted JSON metadata"""
        if obj.metadata_json:
            try:
                metadata = json.loads(obj.metadata_json)
                formatted_json = json.dumps(metadata, indent=2)
                return format_html(
                    '<pre style="max-height: 300px; overflow: auto; background: #f5f5f5; padding: 10px; border: 1px solid #ddd; border-radius: 2px;">{}</pre>',
                    formatted_json
                )
            except:
                return 'Invalid JSON'
        return 'No metadata'
    formatted_metadata.short_description = 'Metadata (Formatted)'
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related('status_fk').order_by('-updated', '-created')


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
    readonly_fields = ('id', 'created', 'updated', 'formatted_metadata')
    fieldsets = (
        ('Basic Information', {
            'fields': ('id', 'name', 'display_name', 'email')
        }),
        ('Metadata', {
            'fields': ('formatted_metadata', 'metadata_json'),
            'classes': ('collapse',)
        }),
    )
    
    def formatted_metadata(self, obj):
        """Display formatted JSON metadata"""
        if obj.metadata_json:
            try:
                metadata = json.loads(obj.metadata_json)
                formatted_json = json.dumps(metadata, indent=2)
                return format_html(
                    '<pre style="max-height: 300px; overflow: auto; background: #f5f5f5; padding: 10px; border: 1px solid #ddd; border-radius: 2px;">{}</pre>',
                    formatted_json
                )
            except:
                return 'Invalid JSON'
        return 'No metadata'
    formatted_metadata.short_description = 'Metadata (Formatted)'


@admin.register(EntityPerson)
class EntityPersonAdmin(admin.ModelAdmin):
    list_display = ('entity', 'person', 'created')
    list_filter = ('created',)
    search_fields = ('entity__title', 'person__name')
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
