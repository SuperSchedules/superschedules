from django.contrib import admin
from django.contrib import messages
from django.conf import settings
from django.db import connection
from django.utils.html import format_html
import requests
import logging
import json
from .models import Source, Event, ServiceToken, SiteStrategy, ScrapingJob, ScrapeBatch

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
        'id',
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
    list_display = ('id', 'title', 'source', 'start_time', 'get_location_display', 'organizer', 'event_status', 'created_at')
    search_fields = ('title', 'description', 'organizer')
    list_filter = ('source', 'event_status', 'event_attendance_mode', 'start_time', 'created_at')
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
            """
            Show current queue status.

            Note: With SQS broker, queue visibility is via AWS Console.
            This action shows task result counts instead.
            """
            from django_celery_results.models import TaskResult

            stats = {
                'PENDING': TaskResult.objects.filter(status='PENDING').count(),
                'STARTED': TaskResult.objects.filter(status='STARTED').count(),
                'SUCCESS': TaskResult.objects.filter(status='SUCCESS').count(),
                'FAILURE': TaskResult.objects.filter(status='FAILURE').count(),
            }

            message = f"Task Status: {stats['PENDING']} pending, {stats['STARTED']} started, {stats['SUCCESS']} success, {stats['FAILURE']} failed"
            self.message_user(request, message, level=messages.INFO)

        show_queue_status.short_description = "Show task statistics"

except ImportError:
    logger.warning("django-celery-results not installed, skipping TaskResult admin")

