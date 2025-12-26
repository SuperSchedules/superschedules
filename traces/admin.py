"""
Django admin configuration for chat debug traces.

Provides:
- ModelAdmin for viewing ChatDebugRun and ChatDebugEvent
- Custom view for running debug chat sessions
- JSON export functionality
"""

import json
from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import path, reverse
from django.http import JsonResponse, StreamingHttpResponse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import ChatDebugRun, ChatDebugEvent


class ChatDebugEventInline(admin.TabularInline):
    """Inline view of events within a run."""
    model = ChatDebugEvent
    extra = 0
    readonly_fields = ['seq', 'stage', 'data_preview', 'latency_ms', 'created_at']
    fields = ['seq', 'stage', 'data_preview', 'latency_ms', 'created_at']
    ordering = ['seq']
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def data_preview(self, obj):
        """Show truncated JSON preview."""
        data_str = json.dumps(obj.data, indent=2)
        if len(data_str) > 200:
            return data_str[:200] + '...'
        return data_str
    data_preview.short_description = 'Data Preview'


@admin.register(ChatDebugRun)
class ChatDebugRunAdmin(admin.ModelAdmin):
    """Admin for viewing chat debug runs."""

    list_display = ['id_short', 'request_preview', 'status_badge', 'total_latency_ms', 'event_count', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['request_text', 'final_answer_text']
    readonly_fields = [
        'id', 'created_at', 'created_by', 'status', 'total_latency_ms',
        'error_message', 'error_stack', 'diagnostics_display',
        'settings_display', 'final_answer_display', 'request_text_display',
    ]
    inlines = [ChatDebugEventInline]

    fieldsets = (
        ('Run Info', {
            'fields': ('id', 'created_at', 'created_by', 'status', 'total_latency_ms'),
        }),
        ('Input', {
            'fields': ('request_text_display', 'settings_display'),
        }),
        ('Output', {
            'fields': ('final_answer_display',),
            'classes': ('collapse',),
        }),
        ('Diagnostics', {
            'fields': ('diagnostics_display',),
            'classes': ('collapse',),
        }),
        ('Errors', {
            'fields': ('error_message', 'error_stack'),
            'classes': ('collapse',),
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('debug-runner/', self.admin_site.admin_view(self.debug_runner_view), name='traces_debug_runner'),
            path('<uuid:run_id>/export/', self.admin_site.admin_view(self.export_run_json), name='traces_export_run'),
        ]
        return custom_urls + urls

    def id_short(self, obj):
        return str(obj.id)[:8]
    id_short.short_description = 'ID'

    def request_preview(self, obj):
        text = obj.request_text[:60] + '...' if len(obj.request_text) > 60 else obj.request_text
        return text
    request_preview.short_description = 'Request'

    def status_badge(self, obj):
        colors = {
            'pending': '#ffc107',
            'running': '#17a2b8',
            'success': '#28a745',
            'error': '#dc3545',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background:{}; color:white; padding:2px 8px; border-radius:4px;">{}</span>',
            color, obj.status
        )
    status_badge.short_description = 'Status'

    def event_count(self, obj):
        return obj.events.count()
    event_count.short_description = 'Events'

    def settings_display(self, obj):
        return format_html('<pre style="max-height:200px; overflow:auto;">{}</pre>',
                          json.dumps(obj.settings, indent=2))
    settings_display.short_description = 'Settings'

    def diagnostics_display(self, obj):
        if not obj.diagnostics:
            return 'No diagnostics'
        return format_html('<pre style="max-height:300px; overflow:auto;">{}</pre>',
                          json.dumps(obj.diagnostics, indent=2))
    diagnostics_display.short_description = 'Diagnostics'

    def final_answer_display(self, obj):
        if not obj.final_answer_text:
            return 'No response'
        return format_html('<pre style="white-space:pre-wrap; max-height:400px; overflow:auto;">{}</pre>',
                          obj.final_answer_text)
    final_answer_display.short_description = 'Response'

    def request_text_display(self, obj):
        return format_html('<pre style="white-space:pre-wrap;">{}</pre>', obj.request_text)
    request_text_display.short_description = 'Request Text'

    def debug_runner_view(self, request):
        """Custom view for running debug chat sessions."""
        return render(request, 'admin/traces/debug_runner.html', {
            'title': 'Chat Debug Runner',
            'opts': self.model._meta,
        })

    def export_run_json(self, request, run_id):
        """Export a run and its events as JSON."""
        run = get_object_or_404(ChatDebugRun, id=run_id)
        events = list(run.events.order_by('seq').values())

        # Convert datetime objects to ISO format
        for event in events:
            if event.get('created_at'):
                event['created_at'] = event['created_at'].isoformat()

        data = {
            'run': {
                'id': str(run.id),
                'created_at': run.created_at.isoformat(),
                'request_text': run.request_text,
                'settings': run.settings,
                'status': run.status,
                'final_answer_text': run.final_answer_text,
                'total_latency_ms': run.total_latency_ms,
                'error_message': run.error_message,
                'diagnostics': run.diagnostics,
            },
            'events': events,
        }

        response = JsonResponse(data, json_dumps_params={'indent': 2})
        response['Content-Disposition'] = f'attachment; filename="debug_run_{run_id}.json"'
        return response

    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Override to add export button and structured event view."""
        extra_context = extra_context or {}
        extra_context['show_export_button'] = True
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(ChatDebugEvent)
class ChatDebugEventAdmin(admin.ModelAdmin):
    """Admin for viewing individual events (mostly for debugging)."""

    list_display = ['id', 'run_link', 'seq', 'stage', 'latency_ms', 'created_at']
    list_filter = ['stage', 'created_at']
    readonly_fields = ['run', 'seq', 'stage', 'data_display', 'latency_ms', 'created_at']

    def run_link(self, obj):
        url = reverse('admin:traces_chatdebugrun_change', args=[obj.run_id])
        return format_html('<a href="{}">{}</a>', url, str(obj.run_id)[:8])
    run_link.short_description = 'Run'

    def data_display(self, obj):
        return format_html('<pre style="max-height:500px; overflow:auto;">{}</pre>',
                          json.dumps(obj.data, indent=2))
    data_display.short_description = 'Data'
