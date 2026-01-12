from django.contrib import admin
from django.contrib import messages
from django.conf import settings
from django.db import connection
from django.utils.html import format_html
import requests
import logging
import json
from .models import Source, Event, ServiceToken, SiteStrategy, ScrapingJob, ScrapeBatch, ChatSession, ChatMessage

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
                            # Use Schema.org aware event creation (returns tuple)
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
    list_filter = ('source', 'venue', 'event_status', 'event_attendance_mode', 'start_time', 'created_at')
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


def reset_to_pending(modeladmin, request, queryset):
    """Reset selected scraping jobs to pending status for retry."""
    count = queryset.update(status='pending', error_message='', locked_by='', locked_at=None)
    modeladmin.message_user(request, f"{count} job(s) reset to pending.")
reset_to_pending.short_description = "Reset to pending (retry)"


@admin.register(ScrapingJob)
class ScrapingJobAdmin(admin.ModelAdmin):
    list_display = ("id", "url", "status", "events_found", "error_message", "created_at")
    search_fields = ("url", "domain")
    list_filter = ("status",)
    actions = [reset_to_pending]


@admin.register(ScrapeBatch)
class ScrapeBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "submitted_by", "created_at")


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ('role', 'content', 'created_at', 'metadata')
    fields = ('role', 'content', 'created_at', 'metadata')
    ordering = ('created_at',)
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'message_count', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active', 'created_at', 'user')
    search_fields = ('title', 'user__username', 'user__email')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ChatMessageInline]
    ordering = ('-updated_at',)

    def message_count(self, obj):
        return obj.messages.count()
    message_count.short_description = 'Messages'


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'session_link', 'role', 'content_preview', 'model_used', 'created_at')
    list_filter = ('role', 'created_at', 'session__user')
    search_fields = ('content', 'session__title', 'session__user__username')
    readonly_fields = ('session', 'role', 'content', 'created_at', 'metadata', 'referenced_events_list')
    ordering = ('-created_at',)

    def content_preview(self, obj):
        return obj.content[:80] + '...' if len(obj.content) > 80 else obj.content
    content_preview.short_description = 'Content'

    def session_link(self, obj):
        from django.urls import reverse
        url = reverse('admin:events_chatsession_change', args=[obj.session.id])
        return format_html('<a href="{}">{}</a>', url, obj.session.title or f'Session {obj.session.id}')
    session_link.short_description = 'Session'

    def model_used(self, obj):
        return obj.metadata.get('model', '-')
    model_used.short_description = 'Model'

    def referenced_events_list(self, obj):
        events = obj.referenced_events.all()[:10]
        if not events:
            return '-'
        return format_html('<br>'.join([f'{e.id}: {e.title[:50]}' for e in events]))
    referenced_events_list.short_description = 'Referenced Events'


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

