"""
Django admin configuration for Location model.
"""

from django.contrib import admin

from locations.models import Location


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    """Admin interface for Location records."""

    list_display = ['name', 'state', 'population', 'latitude', 'longitude', 'lsad', 'geoid']
    list_filter = ['state', 'country_code', 'lsad']
    search_fields = ['name', 'normalized_name', 'geoid']
    ordering = ['-population', 'state', 'name']
    readonly_fields = ['geoid', 'normalized_name', 'created_at', 'updated_at']

    fieldsets = (
        ('Identity', {
            'fields': ('geoid', 'name', 'normalized_name')
        }),
        ('Location', {
            'fields': ('state', 'country_code')
        }),
        ('Coordinates', {
            'fields': ('latitude', 'longitude')
        }),
        ('Metadata', {
            'fields': ('lsad', 'population', 'land_area_sqmi')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
