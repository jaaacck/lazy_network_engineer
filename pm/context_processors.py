from django.db.models import Q
from .models import Status


def status_context(request):
    """Provide status lists used in base templates."""
    task_statuses = Status.objects.filter(
        is_active=True
    ).filter(
        Q(entity_types__contains='task') | Q(entity_types__contains='all')
    ).order_by('order', 'name')

    bulk_statuses = Status.objects.filter(
        is_active=True
    ).filter(
        Q(entity_types__contains='task') | Q(entity_types__contains='subtask') | Q(entity_types__contains='all')
    ).order_by('order', 'name')

    return {
        'task_statuses': task_statuses,
        'bulk_statuses': bulk_statuses,
    }
