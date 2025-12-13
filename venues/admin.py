"""
Venue admin configuration with Grappelli styling.
"""

from django.contrib import admin
from django.db.models import Count

from venues.models import Venue


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    """Admin interface for Venue management."""

    list_display = ['name', 'city', 'state', 'postal_code', 'event_count', 'source_domain', 'created_at']
    list_filter = ['state', 'city', 'created_at']
    search_fields = ['name', 'city', 'state', 'street_address', 'source_domain']
    readonly_fields = ['slug', 'created_at', 'updated_at']
    ordering = ['-created_at']

    fieldsets = (
        ('Venue Identity', {
            'fields': ('name', 'slug', 'canonical_url')
        }),
        ('Address', {
            'fields': ('street_address', 'city', 'state', 'postal_code', 'country')
        }),
        ('Geocoding', {
            'fields': ('latitude', 'longitude'),
            'classes': ('collapse',)
        }),
        ('Source Tracking', {
            'fields': ('source_domain', 'raw_schema'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def get_queryset(self, request):
        """Annotate queryset with event count."""
        queryset = super().get_queryset(request)
        return queryset.annotate(_event_count=Count('events'))

    def event_count(self, obj):
        """Display number of events at this venue."""
        return getattr(obj, '_event_count', obj.events.count())
    event_count.short_description = 'Events'
    event_count.admin_order_field = '_event_count'
