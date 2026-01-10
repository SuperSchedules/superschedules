"""
Venue admin configuration with Grappelli styling.
"""

from django.contrib import admin
from django.db.models import Count

from venues.models import Venue, VenueHours
from venues.geocoding import geocode_venue
from venues.extraction import _clean_street_address


def geolocate_venues(modeladmin, request, queryset):
    """Trigger geocoding for selected venues."""
    success = 0
    skipped = 0
    failed = 0

    for venue in queryset:
        if venue.latitude and venue.longitude:
            skipped += 1
            continue
        if geocode_venue(venue.id):
            success += 1
        else:
            failed += 1

    modeladmin.message_user(request, f"Geocoded: {success}, Skipped (already has coords): {skipped}, Failed: {failed}")
geolocate_venues.short_description = "Geolocate selected venues"


def force_geolocate_venues(modeladmin, request, queryset):
    """Force re-geocoding for selected venues (clears existing coords first)."""
    success = 0
    failed = 0

    for venue in queryset:
        # Clear existing coordinates so geocode_venue will run
        venue.latitude = None
        venue.longitude = None
        venue.save(update_fields=['latitude', 'longitude'])

        if geocode_venue(venue.id):
            success += 1
        else:
            failed += 1

    modeladmin.message_user(request, f"Re-geocoded: {success}, Failed: {failed}")
force_geolocate_venues.short_description = "Force re-geolocate (overwrite existing)"


def cleanup_addresses(modeladmin, request, queryset):
    """Clean up street_address fields that contain full addresses."""
    cleaned = 0
    skipped = 0

    for venue in queryset:
        cleaned_street, extracted_postal = _clean_street_address(
            venue.street_address or "",
            venue.city or "",
            venue.state or "",
            venue.postal_code or ""
        )

        # Check if anything changed
        changed = False
        if cleaned_street != (venue.street_address or ""):
            venue.street_address = cleaned_street
            changed = True
        if extracted_postal and not venue.postal_code:
            venue.postal_code = extracted_postal
            changed = True

        if changed:
            venue.save(update_fields=['street_address', 'postal_code'])
            cleaned += 1
        else:
            skipped += 1

    modeladmin.message_user(request, f"Cleaned: {cleaned}, Already clean: {skipped}")
cleanup_addresses.short_description = "Clean up addresses (remove duplicated city/state/zip)"


class VenueHoursInline(admin.TabularInline):
    """Inline display of venue hours."""
    model = VenueHours
    extra = 0
    fields = ['day_of_week', 'open_time', 'close_time', 'is_closed', 'notes']
    ordering = ['day_of_week']


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    """Admin interface for Venue management."""

    list_display = [
        'id', 'name', 'venue_kind', 'venue_name_quality', 'city', 'state',
        'audience_primary', 'enrichment_status', 'event_count', 'created_at'
    ]
    list_filter = ['venue_kind', 'venue_name_quality', 'audience_primary', 'enrichment_status', 'state', 'city']
    search_fields = ['name', 'city', 'state', 'street_address', 'source_domain']
    readonly_fields = ['slug', 'created_at', 'updated_at', 'last_enriched_at']
    ordering = ['-created_at']
    actions = [geolocate_venues, force_geolocate_venues, cleanup_addresses]
    inlines = [VenueHoursInline]

    fieldsets = (
        ('Venue Identity', {
            'fields': ('name', 'slug', 'website_url', 'canonical_url')
        }),
        ('Classification', {
            'fields': ('venue_kind', 'venue_kind_confidence', 'venue_name_quality'),
        }),
        ('Address', {
            'fields': ('street_address', 'city', 'state', 'postal_code', 'country')
        }),
        ('Geocoding', {
            'fields': ('latitude', 'longitude'),
            'classes': ('collapse',)
        }),
        ('Audience', {
            'fields': ('audience_age_groups', 'audience_tags', 'audience_min_age', 'audience_primary'),
        }),
        ('Content', {
            'fields': ('description', 'kids_summary'),
            'classes': ('collapse',)
        }),
        ('Enrichment Metadata', {
            'fields': ('enrichment_status', 'last_enriched_at', 'website_url_confidence'),
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


@admin.register(VenueHours)
class VenueHoursAdmin(admin.ModelAdmin):
    """Admin for viewing/managing venue hours."""
    list_display = ['venue', 'day_of_week', 'open_time', 'close_time', 'is_closed']
    list_filter = ['day_of_week', 'is_closed']
    search_fields = ['venue__name']
    ordering = ['venue', 'day_of_week']
