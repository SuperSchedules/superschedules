from django.contrib import admin
from django.contrib import messages
from django.conf import settings
from django.db import connection
from django.utils.html import format_html
import requests
import logging
import json
from .models import Event, ServiceToken, SiteStrategy, ScrapingJob, ScrapeHistory, ChatSession, ChatMessage

logger = logging.getLogger(__name__)

# NOTE: SourceAdmin has been removed - venues are now the first-class citizen
# See venues/admin.py for VenueAdmin with scraping capabilities


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'venue', 'start_time', 'get_location_display', 'organizer', 'event_status', 'created_at')
    search_fields = ('title', 'description', 'organizer')
    list_filter = ('venue', 'event_status', 'event_attendance_mode', 'start_time', 'created_at')
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


def mark_healthy(modeladmin, request, queryset):
    """Reset selected scrape histories to healthy status."""
    count = queryset.update(health_status='healthy', consecutive_failures=0)
    modeladmin.message_user(request, f"{count} history record(s) marked as healthy.")
mark_healthy.short_description = "Mark as healthy (reset failures)"


def mark_unscrapable(modeladmin, request, queryset):
    """Mark selected scrape histories as unscrapable."""
    count = queryset.update(health_status='unscrapable')
    modeladmin.message_user(request, f"{count} history record(s) marked as unscrapable.")
mark_unscrapable.short_description = "Mark as unscrapable (stop retrying)"


def mark_paused(modeladmin, request, queryset):
    """Pause scraping for selected URLs."""
    count = queryset.update(health_status='paused')
    modeladmin.message_user(request, f"{count} history record(s) paused.")
mark_paused.short_description = "Pause scraping"


def queue_immediate_scrape(modeladmin, request, queryset):
    """Create immediate scraping jobs for selected URLs."""
    from urllib.parse import urlparse
    from django.utils import timezone

    queued = 0
    skipped = 0

    for history in queryset:
        # Check for existing pending/processing job
        existing = ScrapingJob.objects.filter(
            url=history.url,
            status__in=['pending', 'processing']
        ).exists()

        if existing:
            skipped += 1
            continue

        # Create new job linked to this history
        ScrapingJob.objects.create(
            url=history.url,
            domain=history.domain,
            status='pending',
            venue=history.venue,
            scrape_history=history,
            priority=3,  # Higher priority for admin-triggered jobs
            triggered_by='admin_action',
        )
        queued += 1

    modeladmin.message_user(request, f"Queued {queued} jobs. Skipped {skipped} (already pending).")
queue_immediate_scrape.short_description = "Queue immediate scrape"


class ScrapeHistoryJobInline(admin.TabularInline):
    """Inline showing recent jobs for a ScrapeHistory."""
    model = ScrapingJob
    fk_name = 'scrape_history'
    extra = 0
    fields = ['status', 'events_found', 'error_message', 'created_at', 'completed_at']
    readonly_fields = ['status', 'events_found', 'error_message', 'created_at', 'completed_at']
    ordering = ['-created_at']
    max_num = 10
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ScrapeHistory)
class ScrapeHistoryAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'venue_link', 'url_short', 'health_status', 'success_rate_display',
        'consecutive_failures', 'total_attempts', 'last_scraped_at', 'error_category'
    ]
    list_filter = ['health_status', 'error_category', 'domain']
    search_fields = ['url', 'venue__name', 'domain', 'last_error']
    readonly_fields = [
        'venue', 'url', 'domain', 'total_attempts', 'successful_attempts',
        'consecutive_failures', 'total_events_found', 'first_scraped_at',
        'last_scraped_at', 'last_success_at', 'last_error', 'last_error_at',
        'created_at', 'updated_at'
    ]
    actions = [mark_healthy, mark_unscrapable, mark_paused, queue_immediate_scrape]
    inlines = [ScrapeHistoryJobInline]
    ordering = ['-last_scraped_at']
    list_per_page = 50

    fieldsets = (
        ('URL Info', {
            'fields': ('venue', 'url', 'domain')
        }),
        ('Statistics', {
            'fields': ('total_attempts', 'successful_attempts', 'consecutive_failures', 'total_events_found')
        }),
        ('Timing', {
            'fields': ('first_scraped_at', 'last_scraped_at', 'last_success_at', 'next_scheduled_at')
        }),
        ('Health', {
            'fields': ('health_status', 'error_category', 'last_error', 'last_error_at')
        }),
        ('Agent Notes', {
            'fields': ('agent_notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def venue_link(self, obj):
        """Display venue as a link."""
        from django.urls import reverse
        url = reverse('admin:venues_venue_change', args=[obj.venue.id])
        return format_html('<a href="{}">{}</a>', url, obj.venue.name[:30])
    venue_link.short_description = 'Venue'
    venue_link.admin_order_field = 'venue__name'

    def url_short(self, obj):
        """Display truncated URL."""
        url = obj.url
        if len(url) > 50:
            return url[:50] + '...'
        return url
    url_short.short_description = 'URL'

    def success_rate_display(self, obj):
        """Display success rate with color."""
        rate = obj.success_rate
        if rate >= 80:
            color = '#28a745'
        elif rate >= 50:
            color = '#ffc107'
        else:
            color = '#dc3545'
        return format_html('<span style="color: {};">{:.1f}%</span>', color, rate)
    success_rate_display.short_description = 'Success Rate'


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

