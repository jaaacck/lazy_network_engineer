"""
URL configuration for project_manager project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from pm import views
import os

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.project_list, name='project_list'),
    path('project/new/', views.new_project, name='new_project'),
    path('project/<str:project>/', views.project_detail, name='project_detail'),
    path('project/<str:project>/epic/new/', views.new_epic, name='new_epic'),
    path('project/<str:project>/epic/<str:epic>/', views.epic_detail, name='epic_detail'),
    # Task routes with epic (existing)
    path('project/<str:project>/epic/<str:epic>/new-task/', views.new_task, name='new_task'),
    path('project/<str:project>/epic/<str:epic>/task/<str:task>/', views.task_detail, name='task_detail'),
    path('project/<str:project>/epic/<str:epic>/task/<str:task>/new-subtask/', views.new_subtask, name='new_subtask'),
    path('project/<str:project>/epic/<str:epic>/task/<str:task>/subtask/<str:subtask>/', 
         views.subtask_detail, name='subtask_detail'),
    # Task routes without epic (new)
    path('project/<str:project>/new-task/', views.new_task, name='new_task_no_epic'),
    path('project/<str:project>/task/<str:task>/', views.task_detail_no_epic, name='task_detail_no_epic'),
    path('project/<str:project>/task/<str:task>/new-subtask/', views.new_subtask_no_epic, name='new_subtask_no_epic'),
    path('project/<str:project>/task/<str:task>/subtask/<str:subtask>/', 
         views.subtask_detail_no_epic, name='subtask_detail_no_epic'),
    path('calendar/', views.calendar_view, name='calendar'),
    path('calendar/day/<str:date_str>/', views.calendar_day, name='calendar_day'),
    path('calendar/week/<int:year>/<int:week>/', views.calendar_week, name='calendar_week'),
    path('my-work/', views.my_work, name='my_work'),
    path('today/', views.today_view, name='today'),
    path('search/', views.search_view, name='search'),
    path('kanban/', views.kanban_view, name='kanban'),
    path('kanban/<str:project>/', views.kanban_view, name='kanban_project'),
    path('kanban/<str:project>/<str:epic>/', views.kanban_view, name='kanban_epic'),
    path('notes/', views.notes_list, name='notes_list'),
    path('notes/new/', views.new_note, name='new_note'),
    path('notes/<str:note_id>/', views.note_detail, name='note_detail'),
    path('notes/<str:note_id>/delete/', views.delete_note, name='delete_note'),
    path('people/', views.people_list, name='people_list'),
    path('people/<str:person_id>/', views.person_detail, name='person_detail'),
    path('inbox/', views.inbox_view, name='inbox'),
    path('api/quick-add/', views.quick_add, name='quick_add'),
    path('project/<str:project>/epic/<str:epic>/task/<str:task>/move/', views.move_task, name='move_task'),
    path('project/<str:project>/task/<str:task>/move/', views.move_task_no_epic, name='move_task_no_epic'),
    path('project/<str:project>/epic/<str:epic>/move/', views.move_epic, name='move_epic'),
    path('project/<str:project>/epic/<str:epic>/task/<str:task>/subtask/<str:subtask>/move/', views.move_subtask, name='move_subtask'),
    path('project/<str:project>/task/<str:task>/subtask/<str:subtask>/move/', views.move_subtask_no_epic, name='move_subtask_no_epic'),
    path('api/update-task-schedule/', views.update_task_schedule, name='update_task_schedule'),
    path('api/reorder-items/', views.reorder_items, name='reorder_items'),
    path('api/update-task-status/', views.update_task_status, name='update_task_status'),
    path('api/bulk-update/', views.bulk_update_items, name='bulk_update_items'),
    path('api/whois/', views.whois_query, name='whois_query'),
    path('api/dig/', views.dig_query, name='dig_query'),
    path('api/mac-lookup/', views.mac_lookup, name='mac_lookup'),
    path('api/upload-image/', views.upload_image, name='upload_image'),
    path('api/search-persons/', views.search_persons, name='search_persons'),
]

# Serve uploaded images in development
# Using /uploads/ instead of /static/uploads/ to avoid conflict with Django's
# staticfiles app which intercepts /static/ URLs before URL routing
if settings.DEBUG:
    uploads_dir = os.path.join(settings.DATA_ROOT, 'uploads')
    # Always add URL pattern in DEBUG mode - directory will be created on first upload
    # Serve files from data/uploads/ at /uploads/
    # This matches URLs like /uploads/2026/01/filename.png
    # and serves files from data/uploads/2026/01/filename.png
    urlpatterns.insert(0, re_path(
        r'^uploads/(?P<path>.*)$',
        serve,
        {'document_root': uploads_dir, 'show_indexes': False}
    ))
