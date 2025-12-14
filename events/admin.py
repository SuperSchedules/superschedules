from django.contrib import admin
from django.contrib import messages
from django.conf import settings
from django.urls import path
from django.shortcuts import render
from django.db import connection
from django.utils.html import format_html
import requests
import logging
import json
from .models import Source, Event, ServiceToken, SiteStrategy, ScrapingJob, ScrapeBatch
from .celery_models import KombuMessage, KombuQueue

logger = logging.getLogger(__name__)


def process_sources_via_collector(modeladmin, request, queryset):
    """Admin action to process selected sources via collector API."""
    collector_url = getattr(settings, 'COLLECTOR_URL', 'http://localhost:8001')
    
    results = []
    for source in queryset:
        try:
            # Call collector API to extract events
            response = requests.post(
                f"{collector_url}/extract",
                json={
                    "url": source.base_url,
                    "extraction_hints": {
                        "content_selectors": source.site_strategy.best_selectors if source.site_strategy else None,
                        "additional_hints": {}
                    }
                },
                timeout=180  # Allow time for iframe + calendar pagination
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get('success') and data.get('events'):
                    event_count = len(data['events'])
                    # Process and save events (reusing logic from api/views.py)
                    from events.models import Event
                    
                    for event_data in data['events']:
                        try:
                            # Use Schema.org aware event creation
                            Event.create_with_schema_org_data(event_data, source)
                        except Exception as e:
                            logger.error("Failed to save event: %s", e)
                    
                    # Update source status and last run time
                    from django.utils import timezone
                    source.status = 'active'
                    source.last_run_at = timezone.now()
                    source.save()
                    
                    results.append(f"✅ {source.name}: {event_count} events collected")
                else:
                    results.append(f"⚠️ {source.name}: No events found")
            else:
                results.append(f"❌ {source.name}: API error ({response.status_code})")
                
        except requests.exceptions.RequestException as e:
            results.append(f"❌ {source.name}: Connection failed - {str(e)}")
        except Exception as e:
            results.append(f"❌ {source.name}: Processing failed - {str(e)}")
    
    # Display results to admin user
    if results:
        messages.success(request, "Processing complete:\n" + "\n".join(results))
    else:
        messages.warning(request, "No sources were processed.")

process_sources_via_collector.short_description = "Process selected sources via collector API"


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'base_url',
        'site_strategy',
        'status',
        'last_run_at',
        'date_added',
    )
    fields = (
        'user',
        'name',
        'base_url',
        'site_strategy',
        'search_method',
        'status',
        'event',
        'last_run_at',
        'date_added',
    )
    readonly_fields = ('last_run_at', 'date_added')
    actions = [process_sources_via_collector]
    list_filter = ('status', 'site_strategy', 'search_method')
    search_fields = ('name', 'base_url')


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'source', 'start_time', 'get_location_display', 'organizer', 'event_status')
    search_fields = ('title', 'description', 'organizer')
    list_filter = ('source', 'event_status', 'event_attendance_mode', 'start_time')
    readonly_fields = ('external_id', 'created_at', 'updated_at')
    
    def get_location_display(self, obj):
        """Display location from venue."""
        return obj.get_location_string() or "—"
    get_location_display.short_description = 'Location'


@admin.register(ServiceToken)
class ServiceTokenAdmin(admin.ModelAdmin):
    list_display = ("name", "token", "created_at")
    readonly_fields = ("token", "created_at")


@admin.register(SiteStrategy)
class SiteStrategyAdmin(admin.ModelAdmin):
    list_display = ("domain", "success_rate", "updated_at")
    search_fields = ("domain",)


@admin.register(ScrapingJob)
class ScrapingJobAdmin(admin.ModelAdmin):
    list_display = ("url", "status", "events_found", "created_at")
    search_fields = ("url", "domain")
    list_filter = ("status",)


@admin.register(ScrapeBatch)
class ScrapeBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "submitted_by", "created_at")


# Unregister the default TaskResult admin and register our custom one
try:
    from django_celery_results.models import TaskResult
    admin.site.unregister(TaskResult)

    @admin.register(TaskResult)
    class CustomTaskResultAdmin(admin.ModelAdmin):
        """Enhanced TaskResult admin with queue visibility."""

        list_display = ('task_id_short', 'task_name', 'periodic_task_name', 'status', 'date_created', 'date_done', 'worker')
        list_filter = ('status', 'task_name', 'periodic_task_name', 'worker')
        search_fields = ('task_id', 'task_name', 'periodic_task_name')
        readonly_fields = ('task_id', 'task_name', 'task_args', 'task_kwargs', 'status', 'result', 'traceback', 'date_created', 'date_done', 'worker')
        date_hierarchy = 'date_created'

        # Show most recent tasks first
        ordering = ('-date_created',)

        # Only show recent tasks by default (last 7 days)
        def get_queryset(self, request):
            qs = super().get_queryset(request)
            # Add a check box or filter to show all vs recent
            return qs

        def task_id_short(self, obj):
            """Show shortened task ID for readability."""
            return obj.task_id[:13] + '...' if obj.task_id else '-'
        task_id_short.short_description = 'Task ID'

        # Add custom actions
        actions = ['show_queue_status']

        def show_queue_status(self, request, queryset):
            """Show current queue status from Kombu tables."""
            try:
                with connection.cursor() as cursor:
                    cursor.execute("""
                        SELECT COUNT(*) FROM kombu_message WHERE visible = true
                    """)
                    pending_count = cursor.fetchone()[0]

                self.message_user(request, f"Queue status: {pending_count} pending tasks in queue", level=messages.INFO)
            except Exception as e:
                self.message_user(request, f"Error checking queue: {str(e)}", level=messages.ERROR)

        show_queue_status.short_description = "Check queue status"

except ImportError:
    logger.warning("django-celery-results not installed, skipping TaskResult admin")


# Kombu Queue Monitoring
@admin.register(KombuMessage)
class KombuMessageAdmin(admin.ModelAdmin):
    """
    Admin interface for viewing pending tasks in the Celery queue.

    Read-only access to the kombu_message table.
    """

    list_display = ('id', 'task_name_display', 'task_id_display', 'timestamp', 'visible')
    list_filter = ('visible', 'timestamp')
    readonly_fields = ('id', 'task_name_display', 'task_id_display', 'task_args_display', 'payload_display', 'timestamp', 'visible')
    search_fields = ('id',)
    ordering = ('-timestamp',)

    # Disable add/edit/delete permissions since this is read-only monitoring
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def task_name_display(self, obj):
        """Extract and display task name from payload."""
        info = obj.get_task_info()
        return info.get('task_name', 'Unknown')
    task_name_display.short_description = 'Task Name'

    def task_id_display(self, obj):
        """Extract and display task ID from payload."""
        info = obj.get_task_info()
        task_id = info.get('task_id', '')
        return task_id[:13] + '...' if task_id else '-'
    task_id_display.short_description = 'Task ID'

    def task_args_display(self, obj):
        """Extract and display task args from payload."""
        info = obj.get_task_info()
        args = info.get('args', [])
        kwargs = info.get('kwargs', {})
        return format_html(
            '<strong>Args:</strong> {}<br><strong>Kwargs:</strong> {}',
            json.dumps(args) if args else 'None',
            json.dumps(kwargs) if kwargs else 'None'
        )
    task_args_display.short_description = 'Task Arguments'

    def payload_display(self, obj):
        """Display the full payload for debugging."""
        info = obj.get_task_info()
        if 'error' in info:
            return format_html('<span style="color: red;">Error: {}</span>', info['error'])
        return format_html('<pre>{}</pre>', json.dumps(info, indent=2))
    payload_display.short_description = 'Payload Details'


@admin.register(KombuQueue)
class KombuQueueAdmin(admin.ModelAdmin):
    """
    Admin interface for viewing Celery queues.

    Read-only access to the kombu_queue table.
    """

    list_display = ('id', 'name', 'message_count')
    readonly_fields = ('id', 'name', 'message_count')
    search_fields = ('name',)

    # Disable add/edit/delete permissions
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def message_count(self, obj):
        """Show count of messages in this queue."""
        try:
            with connection.cursor() as cursor:
                # Note: kombu doesn't directly track which queue messages belong to
                # This is a simplified count of all visible messages
                cursor.execute("SELECT COUNT(*) FROM kombu_message WHERE visible = true")
                count = cursor.fetchone()[0]
                return count
        except Exception as e:
            return f"Error: {e}"
    message_count.short_description = 'Pending Messages'

